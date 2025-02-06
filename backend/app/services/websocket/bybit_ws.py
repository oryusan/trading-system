"""
Bybit WebSocket implementation with enhanced public/private endpoint handling.

Features:
- V5 API support with enhanced error handling
- Separate public/private connection handling
- Channel validation by endpoint type
- Rate limit management by endpoint
- Enhanced error recovery
"""

from typing import Dict, Optional, Any, Set, List
import hmac
import base64
import hashlib
import json
import time
from datetime import datetime

from app.services.websocket.base import BaseWebSocket, WebSocketState

class BybitConnectionState(WebSocketState):
    """Extended WebSocket state for Bybit connections."""
    
    def __init__(self):
        super().__init__()
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.ping_interval: int = 20
        self.last_pong: Optional[datetime] = None
        self.login_time: Optional[datetime] = None
        self.category: str = "linear"
        self.public_rate_limit: int = 100   # Higher limit for public endpoints
        self.private_rate_limit: int = 30    # Lower limit for private endpoints
        self.order_book_synced: bool = False
        self.sequence_number: Optional[int] = None

class BybitWebSocket(BaseWebSocket):
    """
    Bybit WebSocket client implementation for V5 API.
    
    Features:
    - V5 API endpoints
    - Enhanced error handling
    - Comprehensive logging
    - Rate limiting
    - Connection recovery
    """

    # V5 API Public Channels
    PUBLIC_CHANNELS = {
        "orderbook.1",
        "orderbook.50",
        "tickers",
        "trades",
        "kline.1",
        "kline.3", 
        "kline.5",
        "kline.15",
        "kline.30",
        "kline.60",
        "kline.120",
        "kline.240",
        "kline.360",
        "kline.720",
        "kline.D",
        "kline.W",
        "kline.M"
    }
    
    # V5 API Private Channels
    PRIVATE_CHANNELS = {
        "position",
        "execution",
        "order",
        "wallet",
        "trade"
    }

    def __init__(
        self,
        ws_type: WebSocketType,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ):
        """Initialize BybitWebSocket with configuration validation."""
        try:
            if ws_type == WebSocketType.PRIVATE and not all([api_key, api_secret]):
                raise ValidationError(
                    "API credentials required for private WebSocket",
                    context={
                        "ws_type": ws_type,
                        "has_key": bool(api_key),
                        "has_secret": bool(api_secret)
                    }
                )

            base_url = (
                "wss://stream-testnet.bybit.com" if testnet 
                else "wss://stream.bybit.com"
            )
            url = f"{base_url}/v5/private" if ws_type == WebSocketType.PRIVATE else f"{base_url}/v5/public/linear"
            
            super().__init__(url=url)
            self.ws_type = ws_type
            self.api_key = api_key
            self.api_secret = api_secret
            self.testnet = testnet
            
            self.state = BybitConnectionState()
            self._rate_limit = (
                self.state.private_rate_limit 
                if ws_type == WebSocketType.PRIVATE 
                else self.state.public_rate_limit
            )
            
            self.logger = logger.getChild(f"bybit_ws_{ws_type.value}")

        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to initialize BybitWebSocket",
                context={
                    "ws_type": ws_type,
                    "testnet": testnet,
                    "error": str(e)
                }
            )

    async def _authenticate(self) -> None:
        """
        Authenticate private WebSocket connection using V5 API.
        
        Raises:
            AuthenticationError: If authentication fails
            WebSocketError: If connection issues occur
        """
        if self.ws_type != WebSocketType.PRIVATE:
            return

        try:
            expires = int((time.time() + 10) * 1000)
            
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                f"GET/realtime{expires}".encode('utf-8'),
                digestmod='sha256'
            ).hexdigest()
            
            auth_msg = {
                "req_id": self.generate_subscription_id(),
                "op": "auth",
                "args": [self.api_key, expires, signature]
            }

            if not self.ws:
                raise WebSocketError(
                    "WebSocket not connected for authentication",
                    context={"ws_type": self.ws_type}
                )
                
            await self.ws.send(json.dumps(auth_msg))
            self.state.auth_attempts += 1
            
            self.logger.info(
                "Sent authentication message",
                extra={
                    "attempt": self.state.auth_attempts,
                    "expires": expires
                }
            )

        except WebSocketError:
            raise
        except Exception as e:
            raise AuthenticationError(
                "Failed to authenticate WebSocket",
                context={
                    "attempt": self.state.auth_attempts,
                    "error": str(e)
                }
            )

    def _validate_channel(self, topic: str) -> None:
        """
        Validate channel compatibility with endpoint type.
        
        Args:
            topic: Channel to validate
            
        Raises:
            ValidationError: If channel invalid for endpoint
        """
        base_channel = topic.split('.')[0] if '.' in topic else topic
        
        if self.ws_type == WebSocketType.PUBLIC:
            if base_channel not in self.PUBLIC_CHANNELS:
                raise ValidationError(
                    "Invalid channel for public endpoint",
                    context={
                        "channel": base_channel,
                        "valid_channels": list(self.PUBLIC_CHANNELS)
                    }
                )
        else:
            if base_channel not in self.PRIVATE_CHANNELS:
                raise ValidationError(
                    "Invalid channel for private endpoint",
                    context={
                        "channel": base_channel,
                        "valid_channels": list(self.PRIVATE_CHANNELS)
                    }
                )

    async def subscribe(self, topic: str) -> None:
        """
        Subscribe to a Bybit V5 API channel.
        
        Args:
            topic: Channel to subscribe to
            
        Raises:
            ValidationError: If topic invalid
            WebSocketError: If subscription fails
        """
        try:
            if not topic:
                raise ValidationError(
                    "Topic cannot be empty",
                    context={"topic": topic}
                )
                
            self._validate_channel(topic)
                
            if not self.ws:
                raise WebSocketError(
                    "WebSocket not connected for subscription",
                    context={"topic": topic}
                )

            sub_msg = {
                "req_id": self.generate_subscription_id(),
                "op": "subscribe",
                "args": [f"{topic}.{self.state.category}"]
            }

            if topic.startswith("orderbook"):
                sub_msg["args"].append({
                    "sequenceStart": self.state.sequence_number
                })
            
            await self.ws.send(json.dumps(sub_msg))
            self.state.subscribed_channels.add(topic)
            
            self.logger.info(
                "Subscribed to topic",
                extra={
                    "topic": topic,
                    "category": self.state.category,
                    "total_subs": len(self.state.subscribed_channels)
                }
            )

        except (ValidationError, WebSocketError):
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to subscribe to topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def unsubscribe(self, topic: str) -> None:
        """
        Unsubscribe from a Bybit V5 API channel.
        
        Args:
            topic: Channel to unsubscribe from
            
        Raises:
            ValidationError: If topic invalid
            WebSocketError: If unsubscription fails
        """
        try:
            if not topic:
                raise ValidationError(
                    "Topic cannot be empty",
                    context={"topic": topic}
                )
                
            self._validate_channel(topic)
                
            if not self.ws:
                raise WebSocketError(
                    "WebSocket not connected for unsubscription",
                    context={"topic": topic}
                )

            unsub_msg = {
                "req_id": self.generate_subscription_id(),
                "op": "unsubscribe",
                "args": [f"{topic}.{self.state.category}"]
            }
            
            await self.ws.send(json.dumps(unsub_msg))
            self.state.subscribed_channels.remove(topic)
            
            self.logger.info(
                "Unsubscribed from topic",
                extra={
                    "topic": topic,
                    "total_subs": len(self.state.subscribed_channels)
                }
            )

        except (ValidationError, WebSocketError):
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to unsubscribe from topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def _handle_public_message(self, message: Dict) -> None:
        """Process public endpoint messages with sequence tracking."""
        try:
            topic = message.get("topic", "")
            
            if topic.startswith("orderbook"):
                sequence = message.get("sequence")
                if sequence:
                    if not self.state.sequence_number:
                        self.state.sequence_number = sequence
                        self.state.order_book_synced = True
                    elif sequence <= self.state.sequence_number:
                        return
                    self.state.sequence_number = sequence
            
            self.state.last_message = datetime.utcnow()

        except Exception as e:
            raise WebSocketError(
                "Failed to handle public message",
                context={
                    "message": message,
                    "error": str(e)
                }
            )

    async def _handle_private_message(self, message: Dict) -> None:
        """Process private endpoint messages with state tracking."""
        try:
            if message.get("op") == "auth":
                self.state.authenticated = True
                self.state.login_time = datetime.utcnow()
                self.logger.info(
                    "Authentication confirmed",
                    extra={"login_time": self.state.login_time.isoformat()}
                )
            
            self.state.last_message = datetime.utcnow()

        except Exception as e:
            raise WebSocketError(
                "Failed to handle private message",
                context={
                    "message": message,
                    "error": str(e)
                }
            )

    async def process_message(self, message: Dict) -> None:
        """Process incoming Bybit WebSocket message with error handling."""
        try:
            if "success" in message:
                if not message["success"]:
                    raise WebSocketError(
                        message.get("ret_msg", "Operation failed"),
                        context={"message": message}
                    )
                elif "op" in message and message["op"] == "auth":
                    self.state.authenticated = True
                    self.state.login_time = datetime.utcnow()
                    self.logger.info(
                        "Authentication successful",
                        extra={
                            "attempts": self.state.auth_attempts,
                            "login_time": self.state.login_time.isoformat()
                        }
                    )
                return

            if "topic" in message and "data" in message:
                if self.ws_type == WebSocketType.PUBLIC:
                    await self._handle_public_message(message)
                else:
                    await self._handle_private_message(message)
                
                topic = message["topic"].split('.')[0]
                if topic in self.callbacks:
                    try:
                        await self.callbacks[topic](message["data"])
                    except Exception as e:
                        await handle_api_error(
                            error=e,
                            context={
                                "topic": topic,
                                "message_type": "callback"
                            },
                            log_message="Callback processing failed"
                        )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={"message": message},
                log_message="Message processing failed"
            )

    async def close(self) -> None:
        """Close WebSocket connection and cleanup resources."""
        try:
            await super().close()
            self.logger.info("Closed Bybit WebSocket connection")
                
        except Exception as e:
            self.logger.error(
                "Error during cleanup",
                extra={"error": str(e)}
            )
            raise WebSocketError(
                "Failed to cleanup resources",
                context={"error": str(e)}
            )

    async def get_status(self) -> Dict:
        """Get detailed connection status information."""
        status = await super().get_status()
        status.update({
            "authenticated": self.state.authenticated,
            "auth_attempts": self.state.auth_attempts,
            "login_time": self.state.login_time.isoformat() if self.state.login_time else None,
            "ping_interval": self.state.ping_interval,
            "last_pong": self.state.last_pong.isoformat() if self.state.last_pong else None,
            "category": self.state.category,
            "rate_limit": self._rate_limit,
            "order_book_synced": self.state.order_book_synced,
            "sequence_number": self.state.sequence_number
        })
        return status

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    WebSocketError,
    ValidationError,
    AuthenticationError
)
from app.core.errors.handlers import handle_api_error
from app.core.references import WebSocketType
from app.core.logging.logger import get_logger

logger = get_logger(__name__)