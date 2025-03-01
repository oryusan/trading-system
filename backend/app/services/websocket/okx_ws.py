"""
OKX WebSocket implementation with enhanced error handling.

This module implements the OKXWebSocket class that extends BaseWebSocket,
providing OKX-specific subscription, authentication, and message processing logic.
High-level methods such as _sign_request, _execute_request, subscribe, unsubscribe,
and process_message are wrapped with a centralized error handler to standardize error context.
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
from app.core.errors.base import ValidationError, WebSocketError, ExchangeError, RequestException
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType

logger = get_logger(__name__)


class OKXConnectionState(WebSocketState):
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


class OKXWebSocket(BaseWebSocket):
    """
    OKX WebSocket client with endpoint-specific handling.

    This client supports both public and private endpoints.
    High-level methods are wrapped with error_handler to standardize error reporting.
    """
    # Valid channels for public endpoints.
    PUBLIC_CHANNELS: Set[str] = {"tickers", "trades", "orderbook", "candle1m", "mark-price"}
    # Valid channels for private endpoints.
    PRIVATE_CHANNELS: Set[str] = {"account", "positions", "orders", "orders-algo", "balance_and_position"}

    @error_handler
    def __init__(
        self,
        ws_type: str,  # Expected values: "public" or "private"
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ) -> None:
        base_url = "wss://ws.okx.com" if not testnet else "wss://wsaws.okx.com"
        url = f"{base_url}/ws/v5/private" if ws_type == "private" else f"{base_url}/ws/v5/public"
        super().__init__(url=url)
        self.ws_type = ws_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.exchange_type = ExchangeType.OKX
        self.logger = get_logger("okx_ws")
        self.state = OKXConnectionState()

    @error_handler(
        context_extractor=lambda self, timestamp, method, endpoint, data=None: {
            "timestamp": timestamp, "method": method, "endpoint": endpoint, "data": data
        },
        log_message="_sign_request failed"
    )
    async def _sign_request(
        self, timestamp: str, method: str, endpoint: str, data: Dict = None
    ) -> Dict[str, str]:
        """
        Sign OKX API request.

        Combines the timestamp, method, endpoint, and (if present) data to produce a signature.
        """
        body = json.dumps(data) if data else ""
        message = f"{timestamp}{method.upper()}{endpoint}{body}"
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).digest()
        ).decode("utf-8")
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }
        if self.testnet:
            headers["x-simulated-trading"] = "1"
        return headers

    @error_handler(
        context_extractor=lambda self, method, endpoint, data, params: {
            "method": method, "endpoint": endpoint, "data": data, "params": params
        },
        log_message="_execute_request failed"
    )
    async def _execute_request(
        self, method: str, endpoint: str, data: Dict = None, params: Dict = None
    ) -> Dict:
        if self.session is None:
            await self.connect()
        url = f"{self.url}{endpoint}"
        timestamp = datetime.utcnow().isoformat("T", "milliseconds") + "Z"
        headers = await self._sign_request(timestamp, method, endpoint, data)
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
                if result.get("code") != "0":
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
        context_extractor=lambda self, topic: {"topic": topic},
        log_message="Subscription failed"
    )
    async def subscribe(self, topic: str) -> None:
        if not topic:
            raise ValidationError("Topic cannot be empty", context={"topic": topic})
        if self.ws_type == "public" and topic not in self.PUBLIC_CHANNELS:
            raise ValidationError("Invalid public channel", context={"topic": topic, "valid_channels": list(self.PUBLIC_CHANNELS)})
        if self.ws_type == "private" and topic not in self.PRIVATE_CHANNELS:
            raise ValidationError("Invalid private channel", context={"topic": topic, "valid_channels": list(self.PRIVATE_CHANNELS)})
        sub_msg = {"op": "subscribe", "args": [topic]}
        await self.ws.send(json.dumps(sub_msg))
        self.logger.info("Subscribed to topic", extra={"topic": topic})
        self.state.subscribed_channels.add(topic)

    @error_handler(
        context_extractor=lambda self, topic: {"topic": topic},
        log_message="Unsubscription failed"
    )
    async def unsubscribe(self, topic: str) -> None:
        if not topic:
            raise ValidationError("Topic cannot be empty", context={"topic": topic})
        unsub_msg = {"op": "unsubscribe", "args": [topic]}
        await self.ws.send(json.dumps(unsub_msg))
        self.logger.info("Unsubscribed from topic", extra={"topic": topic})
        self.state.subscribed_channels.discard(topic)

    @error_handler(
        context_extractor=lambda self, message: {"message": message},
        log_message="Failed to process incoming message"
    )
    async def process_message(self, message: Dict[str, Any]) -> None:
        self.logger.debug("Processing message", extra={"message": message})
        topic = message.get("topic")
        if topic and topic in self.callbacks:
            await self.callbacks[topic](message.get("data", {}))

    @error_handler(
        context_extractor=lambda self: {"ws_type": self.ws_type, "url": self.url},
        log_message="Authentication failed"
    )
    async def _authenticate(self) -> None:
        if self.ws_type != "private":
            return
        timestamp = datetime.utcnow().isoformat("T", "milliseconds") + "Z"
        auth_args = {
            "apiKey": self.api_key,
            "passphrase": self.passphrase,
            "timestamp": timestamp,
            "sign": (await self._sign_request(timestamp, "GET", "/"))
        }
        auth_msg = {"op": "login", "args": [auth_args]}
        await self.ws.send(json.dumps(auth_msg))
        self.state.authenticated = True
        self.logger.info("Authenticated successfully", extra={"timestamp": timestamp})

    @error_handler(
        context_extractor=lambda self: {"dummy": "okx_ws_rate_limit"},
        log_message="Rate limit handling failed"
    )
    async def _handle_rate_limit(self) -> None:
        min_interval = 1.0 / settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND
        now = time.time()
        last = self.state.last_ping.timestamp() if self.state.last_ping else now
        elapsed = now - last
        if elapsed < min_interval:
            for retry in range(3):
                wait_time = (min_interval - elapsed) * (2 ** retry)
                await asyncio.sleep(wait_time)
                now = time.time()
                elapsed = now - (self.state.last_ping.timestamp() if self.state.last_ping else now)
                if elapsed >= min_interval:
                    break
            else:
                raise RateLimitError("Rate limit exceeded", context={"rate_limit": settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND, "elapsed": elapsed})
        self.state.last_ping = datetime.fromtimestamp(now)
