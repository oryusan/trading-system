"""
Bitget WebSocket implementation with enhanced public/private endpoint handling.

Features:
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

class BitgetConnectionState(WebSocketState):
    """Extended WebSocket state for Bitget connections."""
    
    def __init__(self):
        super().__init__()
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.ping_interval: int = 20
        self.last_pong: Optional[datetime] = None
        self.login_time: Optional[datetime] = None
        self.public_rate_limit: int = 100
        self.private_rate_limit: int = 30
        self.order_book_synced: bool = False
        self.sequence_number: Optional[int] = None

class BitgetWebSocket(BaseWebSocket):
    """Bitget WebSocket client with endpoint-specific handling."""

    PUBLIC_CHANNELS = {
        "ticker",
        "trade",
        "orderbook",
        "candle1m",
        "candle5m",
        "candle15m",
        "candle30m",
        "candle1H",
        "candle4H",
        "candle6H",
        "candle12H",
        "candle1D",
        "candle1W"
    }
    
    PRIVATE_CHANNELS = {
        "orders",
        "positions",
        "account",
        "deposit",
        "withdrawal",
        "execution"
    }

    def __init__(
        self,
        ws_type: WebSocketType,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ):
        """Initialize BitgetWebSocket with configuration validation."""
        try:
            if ws_type == WebSocketType.PRIVATE and not all([api_key, api_secret, passphrase]):
                self.logger.error(
                    "Missing API credentials for private WebSocket",
                    extra={
                        "ws_type": ws_type,
                        "has_key": bool(api_key),
                        "has_secret": bool(api_secret),
                        "has_passphrase": bool(passphrase)
                    }
                )
                raise ValidationError(
                    "API credentials required for private WebSocket",
                    context={
                        "ws_type": ws_type,
                        "has_key": bool(api_key),
                        "has_secret": bool(api_secret),
                        "has_passphrase": bool(passphrase)
                    }
                )

            base_url = "wss://ws.bitget.com/v2/ws"
            url = f"{base_url}/private" if ws_type == WebSocketType.PRIVATE else f"{base_url}/public"
            
            super().__init__(url=url)
            self.ws_type = ws_type
            self.api_key = api_key
            self.api_secret = api_secret
            self.passphrase = passphrase
            self.testnet = testnet
            self.product_type = 'SUSDT-FUTURES' if testnet else 'USDT-FUTURES'
            
            self.state = BitgetConnectionState()
            self._rate_limit = (
                self.state.private_rate_limit 
                if ws_type == WebSocketType.PRIVATE 
                else self.state.public_rate_limit
            )
            
            self.logger = logger.getChild(f"bitget_ws_{ws_type.value}")

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(
                "Failed to initialize BitgetWebSocket",
                extra={
                    "ws_type": ws_type,
                    "testnet": testnet,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to initialize BitgetWebSocket",
                context={
                    "ws_type": ws_type,
                    "testnet": testnet,
                    "error": str(e)
                }
            )

    async def _authenticate(self) -> None:
        """Authenticate private WebSocket connection."""
        if self.ws_type != WebSocketType.PRIVATE:
            return

        try:
            timestamp = str(int(time.time()))
            message = timestamp + self.api_key + "5000"
            
            signature = base64.b64encode(
                hmac.new(
                    self.api_secret.encode('utf-8'),
                    message.encode('utf-8'),
                    digestmod=hashlib.sha256
                ).digest()
            ).decode()
            
            auth_msg = {
                "op": "login",
                "args": [{
                    "apiKey": self.api_key,
                    "passphrase": self.passphrase,
                    "timestamp": timestamp,
                    "sign": signature
                }]
            }

            if not self.ws:
                self.logger.error(
                    "WebSocket not connected for authentication",
                    extra={"ws_type": self.ws_type}
                )
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
                    "timestamp": timestamp
                }
            )

        except WebSocketError:
            raise
        except Exception as e:
            self.logger.error(
                "Failed to authenticate WebSocket",
                extra={
                    "attempt": self.state.auth_attempts,
                    "error": str(e)
                }
            )
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
                self.logger.error(
                    "Invalid channel for public endpoint",
                    extra={
                        "channel": base_channel,
                        "valid_channels": list(self.PUBLIC_CHANNELS)
                    }
                )
                raise ValidationError(
                    "Invalid channel for public endpoint",
                    context={
                        "channel": base_channel,
                        "valid_channels": list(self.PUBLIC_CHANNELS)
                    }
                )
        else:
            if base_channel not in self.PRIVATE_CHANNELS:
                self.logger.error(
                    "Invalid channel for private endpoint",
                    extra={
                        "channel": base_channel,
                        "valid_channels": list(self.PRIVATE_CHANNELS)
                    }
                )
                raise ValidationError(
                    "Invalid channel for private endpoint",
                    context={
                        "channel": base_channel,
                        "valid_channels": list(self.PRIVATE_CHANNELS)
                    }
                )

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a specific channel."""
        try:
            if not topic:
                self.logger.error(
                    "Topic cannot be empty",
                    extra={"topic": topic}
                )
                raise ValidationError(
                    "Topic cannot be empty",
                    context={"topic": topic}
                )
                
            self._validate_channel(topic)
                
            if not self.ws:
                self.logger.error(
                    "WebSocket not connected for subscription",
                    extra={"topic": topic}
                )
                raise WebSocketError(
                    "WebSocket not connected for subscription",
                    context={"topic": topic}
                )

            sub_msg = {
                "op": "subscribe",
                "args": [{
                    "channel": topic,
                    "instType": self.product_type,
                    "instId": "default"
                }]
            }

            if topic.startswith("orderbook"):
                sub_msg["args"][0]["sequenceStart"] = self.state.sequence_number
            
            await self.ws.send(json.dumps(sub_msg))
            self.state.subscribed_channels.add(topic)
            
            self.logger.info(
                "Subscribed to topic",
                extra={
                    "topic": topic,
                    "total_subs": len(self.state.subscribed_channels)
                }
            )

        except (WebSocketError, ValidationError):
            raise
        except Exception as e:
            self.logger.error(
                "Failed to subscribe to topic",
                extra={
                    "topic": topic,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to subscribe to topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a specific channel."""
        try:
            if not topic:
                self.logger.error(
                    "Topic cannot be empty",
                    extra={"topic": topic}
                )
                raise ValidationError(
                    "Topic cannot be empty",
                    context={"topic": topic}
                )
                
            self._validate_channel(topic)
                
            if not self.ws:
                self.logger.error(
                    "WebSocket not connected for unsubscription",
                    extra={"topic": topic}
                )
                raise WebSocketError(
                    "WebSocket not connected for unsubscription",
                    context={"topic": topic}
                )

            unsub_msg = {
                "op": "unsubscribe",
                "args": [{
                    "channel": topic,
                    "instType": self.product_type,
                    "instId": "default"
                }]
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

        except (WebSocketError, ValidationError):
            raise
        except Exception as e:
            self.logger.error(
                "Failed to unsubscribe from topic",
                extra={
                    "topic": topic,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to unsubscribe from topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    def _handle_public_message(self, message: Dict) -> None:
        """Process public endpoint message."""
        try:
            topic = message.get("topic", "")
            
            if topic.startswith("orderbook"):
                sequence = message.get("data", [{}])[0].get("sequence")
                if sequence:
                    if not self.state.sequence_number:
                        self.state.sequence_number = sequence
                        self.state.order_book_synced = True
                    elif sequence <= self.state.sequence_number:
                        return
                    self.state.sequence_number = sequence
            
            self.state.last_message = datetime.utcnow()

        except Exception as e:
            self.logger.error(
                "Failed to handle public message",
                extra={
                    "message": message,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to handle public message",
                context={
                    "message": message,
                    "error": str(e)
                }
            )

    def _handle_private_message(self, message: Dict) -> None:
        """Process private endpoint message."""
        try:
            if message.get("event") == "login":
                self.state.authenticated = True
                self.state.login_time = datetime.utcnow()
                self.logger.info(
                    "Authentication confirmed",
                    extra={"login_time": self.state.login_time.isoformat()}
                )
            
            self.state.last_message = datetime.utcnow()

        except Exception as e:
            self.logger.error(
                "Failed to handle private message",
                extra={
                    "message": message,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to handle private message",
                context={
                    "message": message,
                    "error": str(e)
                }
            )

    async def process_message(self, message: Dict) -> None:
        """Process incoming WebSocket message."""
        try:
            if "event" in message:
                event_type = message["event"]
                
                if event_type == "error":
                    self.logger.error(
                        "WebSocket error event",
                        extra={"message": message}
                    )
                    raise WebSocketError(
                        message.get("msg", "Unknown error"),
                        context=message
                    )
                    
                elif event_type == "login":
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
                    
                elif event_type == "pong":
                    self.state.last_pong = datetime.utcnow()
                    return

            if "data" in message and "topic" in message:
                if self.ws_type == WebSocketType.PUBLIC:
                    self._handle_public_message(message)
                else:
                    self._handle_private_message(message)
                    
                topic = message["topic"]
                if topic in self.callbacks:
                    try:
                        await self.callbacks[topic](message["data"])
                    except Exception as e:
                        self.logger.error(
                            "Callback processing failed",
                            extra={
                                "topic": topic,
                                "message_type": "callback",
                                "error": str(e)
                            }
                        )
                        await handle_api_error(
                            error=e,
                            context={
                                "topic": topic,
                                "message_type": "callback"
                            },
                            log_message="Callback processing failed"
                        )

        except Exception as e:
            self.logger.error(
                "Message processing failed",
                extra={
                    "message": message,
                    "error": str(e)
                }
            )
            await handle_api_error(
                error=e,
                context={"message": message},
                log_message="Message processing failed"
            )

    async def close(self) -> None:
        """Close WebSocket connection and cleanup resources."""
        try:
            await super().close()
            self.logger.info("Closed Bitget WebSocket connection")
                
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