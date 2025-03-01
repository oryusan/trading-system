"""
Bitget WebSocket client implementation.

This module implements the BitgetWebSocket class that extends BaseWebSocket,
providing Bitget-specific subscription, authentication, and message processing logic.
It applies centralized error handling via decorators to standardize error context.
"""

import asyncio
import json
import hmac
import base64
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Set

from app.services.websocket.base_ws import BaseWebSocket, WebSocketState
from app.core.errors.decorators import error_handler
from app.core.errors.base import ValidationError, WebSocketError, ExchangeError, RequestException, RateLimitError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType

logger = get_logger(__name__)

class BitgetConnectionState(WebSocketState):
    """Extended WebSocket state for OKX connections."""
    def __init__(self) -> None:
        super().__init__()
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.ping_interval: int = 20
        self.last_pong: Optional[datetime] = None
        self.login_time: Optional[datetime] = None
        self.instrument_types: Set[str] = set()
        self.public_rate_limit: int = 100
        self.private_rate_limit: int = 30
        self.order_book_synced: bool = False
        self.sequence_number: Optional[int] = None


class BitgetWebSocket(BaseWebSocket):
    """
    Bitget WebSocket client for both public and private endpoints.

    This client supports a set of predefined channels. Most high-level operations are
    wrapped with the error_handler decorator to standardize error reporting.
    """
    # Define valid channels for public and private endpoints.
    PUBLIC_CHANNELS: Set[str] = {"ticker", "trade", "orderbook", "candle1m", "candle5m"}
    PRIVATE_CHANNELS: Set[str] = {"orders", "positions", "account"}

    @error_handler
    def __init__(
        self,
        ws_type: str,  # Expected values: "public" or "private"
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ) -> None:
        # Choose URL based on endpoint type.
        base_url = "wss://ws.bitget.com"
        url = f"{base_url}/private" if ws_type == "private" else f"{base_url}/public"
        super().__init__(url=url)
        self.ws_type = ws_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.exchange_type = ExchangeType.Bitget
        self.logger = get_logger("bitget_ws")

    @error_handler(
        context_extractor=lambda self, e, context, log_message, error_msg: {
            "ws_type": self.ws_type, **context
        },
        log_message="_handle_exception failed"
    )
    async def _handle_exception(self, e: Exception, context: dict, log_message: str, error_msg: str) -> None:
        """
        Log an error and then raise an ExchangeError with the given context.
        """
        await handle_api_error(error=e, context=context, log_message=log_message)
        raise ExchangeError(error_msg, context={**context, "error": str(e)})

    @error_handler(
        context_extractor=lambda self, method, endpoint, data, params: {
            "method": method, "endpoint": endpoint, "data": data, "params": params
        },
        log_message="_execute_request failed"
    )
    async def _execute_request(
        self, method: str, endpoint: str, data: Dict = None, params: Dict = None
    ) -> Dict:
        """
        Execute an HTTP request to Bitget API.
        """
        if self.session is None:
            await self.connect()
        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(datetime.now().timestamp() * 1000))
        body = json.dumps(data) if method.upper() == "POST" and data else ""
        headers = await self._sign_request(timestamp, method, endpoint, body)
        await self._handle_rate_limit()
        async with self._request_semaphore:
            async with self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=data if method.upper() == "POST" else None,
                params=params if method.upper() == "GET" else None
            ) as response:
                response.raise_for_status()
                result = await response.json()
                if result.get("code") != "00000":
                    exc = RequestException(
                        result.get("msg", "Unknown error"),
                        context={"response": result, "endpoint": endpoint, "exchange": self.exchange_type}
                    )
                    await handle_api_error(
                        error=exc,
                        context={"response": result, "endpoint": endpoint},
                        log_message=f"API request failed: {endpoint}"
                    )
                    raise exc
                return result.get("data", {})

    @error_handler(
        context_extractor=lambda self, timestamp, method, endpoint, body="": {
            "timestamp": timestamp, "method": method, "endpoint": endpoint, "body": body
        },
        log_message="_sign_request failed"
    )
    async def _sign_request(self, timestamp: str, method: str, endpoint: str, body: str = "") -> Dict[str, str]:
        """
        Sign Bitget API request.
        """
        message = timestamp + method.upper() + endpoint + body
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).digest()
        ).decode("utf-8")
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    @error_handler(
        context_extractor=lambda self, topic: {"topic": topic},
        log_message="Subscription failed"
    )
    async def subscribe(self, topic: str) -> None:
        """
        Subscribe to a topic.
        """
        if not topic:
            raise ValidationError("Topic cannot be empty", context={"topic": topic})
        if self.ws_type == "public" and topic not in self.PUBLIC_CHANNELS:
            raise ValidationError("Invalid public channel", context={"topic": topic, "valid_channels": list(self.PUBLIC_CHANNELS)})
        if self.ws_type == "private" and topic not in self.PRIVATE_CHANNELS:
            raise ValidationError("Invalid private channel", context={"topic": topic, "valid_channels": list(self.PRIVATE_CHANNELS)})
        sub_msg = {"op": "subscribe", "args": [topic]}
        await self.ws.send(json.dumps(sub_msg))
        self.state.subscribed_channels.add(topic)
        self.logger.info("Subscribed to topic", extra={"topic": topic})

    @error_handler(
        context_extractor=lambda self, topic: {"topic": topic},
        log_message="Unsubscription failed"
    )
    async def unsubscribe(self, topic: str) -> None:
        """
        Unsubscribe from a topic.
        """
        if not topic:
            raise ValidationError("Topic cannot be empty", context={"topic": topic})
        unsub_msg = {"op": "unsubscribe", "args": [topic]}
        await self.ws.send(json.dumps(unsub_msg))
        self.state.subscribed_channels.discard(topic)
        self.logger.info("Unsubscribed from topic", extra={"topic": topic})

    @error_handler(
        context_extractor=lambda self, message: {"message": message},
        log_message="Failed to process incoming message"
    )
    async def process_message(self, message: Dict[str, Any]) -> None:
        """
        Process an incoming WebSocket message.
        """
        self.logger.debug("Processing message", extra={"message": message})
        topic = message.get("topic")
        if topic and topic in self.callbacks:
            await self.callbacks[topic](message.get("data", {}))

    @error_handler(
        context_extractor=lambda self: {"ws_type": self.ws_type, "url": self.url},
        log_message="Authentication failed"
    )
    async def _authenticate(self) -> None:
        """
        Authenticate the connection (only for private endpoints).
        """
        if self.ws_type != "private":
            return
        timestamp = str(int(datetime.now().timestamp() * 1000))
        auth_args = {
            "apiKey": self.api_key,
            "passphrase": self.passphrase,
            "timestamp": timestamp,
            # For signing in authentication, we use _sign_request.
            "sign": (await self._sign_request(timestamp, "GET", "/"))
        }
        auth_msg = {"op": "login", "args": [auth_args]}
        await self.ws.send(json.dumps(auth_msg))
        self.state.authenticated = True
        self.logger.info("Authenticated successfully", extra={"timestamp": timestamp})
