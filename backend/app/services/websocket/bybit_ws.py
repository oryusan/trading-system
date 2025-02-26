"""
Bybit WebSocket implementation with enhanced public/private endpoint handling.

Features:
- V5 API support with enhanced error handling via decorators.
- Separate public/private endpoint handling with channel validation.
- Centralized error context for signing, request execution, and message processing.
"""

import asyncio
import json
import hmac
import hashlib
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Any

from app.services.websocket.base_ws import BaseWebSocket, WebSocketState
from app.core.errors.decorators import error_handler
from app.core.errors.base import ValidationError, WebSocketError, RequestException, RateLimitError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType

logger = get_logger(__name__)


class BybitConnectionState(WebSocketState):
    """Extended WebSocket state for Bybit connections."""
    def __init__(self) -> None:
        super().__init__()
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.ping_interval: int = 20
        self.last_pong: Optional[datetime] = None
        self.login_time: Optional[datetime] = None
        self.category: str = "linear"
        self.public_rate_limit: int = 100
        self.private_rate_limit: int = 20
        self.order_book_synced: bool = False
        self.sequence_number: Optional[int] = None


class BybitWebSocket(BaseWebSocket):
    """
    Bybit WebSocket client for V5 API.
    
    This client supports both public and private endpoints.
    Most high-level methods are wrapped with the error_handler decorator
    to standardize error handling.
    """
    PUBLIC_CHANNELS: Set[str] = {"orderbook", "tickers", "trades", "kline.1", "kline.3", "kline.5", "kline.15"}
    PRIVATE_CHANNELS: Set[str] = {"position", "execution", "order", "wallet"}

    @error_handler
    def __init__(
        self,
        ws_type: str,  # Expected values: "public" or "private"
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False
    ) -> None:
        base_url = "wss://stream-testnet.bybit.com" if testnet else "wss://stream.bybit.com"
        # For private endpoints, use a different URL segment if needed.
        if ws_type == "private":
            url = f"{base_url}/v5/private"
        else:
            url = f"{base_url}/v5/public/linear"
        super().__init__(url=url)
        self.ws_type = ws_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.exchange_type = ExchangeType.BYBIT
        self.logger = get_logger("bybit_ws")
        # Use an extended connection state.
        self.state = BybitConnectionState()

    @error_handler(
        context_extractor=lambda self, timestamp, method, endpoint, data: {
            "timestamp": timestamp, "method": method, "endpoint": endpoint, "data": data
        },
        log_message="_sign_request failed"
    )
    async def _sign_request(
        self, timestamp: str, method: str, endpoint: str, data: Dict = None
    ) -> Dict[str, str]:
        body = json.dumps(data) if data else ""
        message = timestamp + self.api_key + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type": "application/json"
        }

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
        timestamp = str(int(time.time() * 1000))
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
                if result.get("retCode", 0) != 0:
                    exc = RequestException(
                        result.get("retMsg", "Unknown error"),
                        context={"response": result, "endpoint": endpoint, "exchange": self.exchange_type}
                    )
                    await handle_api_error(
                        error=exc,
                        context={"response": result, "endpoint": endpoint},
                        log_message=f"API request failed: {endpoint}"
                    )
                    raise exc
                return result.get("result", {})

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
        self.state.subscribed_channels.add(topic)
        self.logger.info("Subscribed to topic", extra={"topic": topic})

    @error_handler(
        context_extractor=lambda self, topic: {"topic": topic},
        log_message="Unsubscription failed"
    )
    async def unsubscribe(self, topic: str) -> None:
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
        timestamp = str(int(time.time() * 1000))
        auth_args = {
            "apiKey": self.api_key,
            "timestamp": timestamp,
            "sign": (await self._sign_request(timestamp, "GET", "/"))
        }
        auth_msg = {"op": "auth", "args": [auth_args]}
        await self.ws.send(json.dumps(auth_msg))
        self.state.authenticated = True
        self.logger.info("Authenticated successfully", extra={"timestamp": timestamp})

    @error_handler(
        context_extractor=lambda self: {"dummy": "bybit_ws_rate_limit"},
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
