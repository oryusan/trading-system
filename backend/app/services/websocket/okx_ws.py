"""
OKX WebSocket implementation with enhanced public/private endpoint handling.

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

class OKXConnectionState(WebSocketState):
    """Extended WebSocket state for OKX connections."""
    
    def __init__(self):
        super().__init__()
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.ping_interval: int = 20
        self.last_pong: Optional[datetime] = None
        self.login_time: Optional[datetime] = None
        self.instTypes: List[str] = []
        self.public_rate_limit: int = 100   # Higher limit for public endpoints
        self.private_rate_limit: int = 30    # Lower limit for private endpoints
        self.order_book_synced: bool = False
        self.sequence_number: Optional[int] = None

class OKXWebSocket(BaseWebSocket):
    """
    OKX WebSocket client with endpoint-specific handling.
    
    Features:
    - Separate public/private endpoints
    - Channel validation
    - Custom rate limits
    - Enhanced error handling
    """

    # Define valid channels for each endpoint type
    PUBLIC_CHANNELS = {
        "tickers",
        "trades",
        "books",
        "books5",
        "books-l2-tbt",
        "candle1m",
        "candle3m",
        "candle5m",
        "candle15m",
        "candle30m",
        "candle1H",
        "candle4H",
        "candle6H",
        "candle12H",
        "candle1D",
        "candle1W",
        "mark-price",
        "estimated-price",
        "platform-24h-volume",
        "funding-rate"
    }
    
    PRIVATE_CHANNELS = {
        "account",
        "positions",
        "orders",
        "orders-algo",
        "algo-advance",
        "liquidation-warning",
        "account-greeks",
        "balance_and_position"
    }

    def __init__(
        self,
        ws_type: WebSocketType,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ):
        """Initialize OKXWebSocket with endpoint-specific configuration."""
        try:
            # Validate private endpoint credentials
            if ws_type == WebSocketType.PRIVATE:
                if not all([api_key, api_secret, passphrase]):
                    raise ValidationError(
                        "API credentials required for private WebSocket",
                        context={
                            "ws_type": ws_type,
                            "has_key": bool(api_key),
                            "has_secret": bool(api_secret),
                            "has_passphrase": bool(passphrase)
                        }
                    )

            # Get appropriate WebSocket URL
            base_url = "wss://ws.okx.com" if not testnet else "wss://wsaws.okx.com"
            url = f"{base_url}/ws/v5/private" if ws_type == WebSocketType.PRIVATE else f"{base_url}/ws/v5/public"
            
            super().__init__(url=url)
            self.ws_type = ws_type
            self.api_key = api_key
            self.api_secret = api_secret
            self.passphrase = passphrase
            self.testnet = testnet
            
            # Override base WebSocketState with OKX-specific version
            self.state = OKXConnectionState()
            
            # Set rate limit based on endpoint type
            self._rate_limit = (
                self.state.private_rate_limit 
                if ws_type == WebSocketType.PRIVATE 
                else self.state.public_rate_limit
            )
            
            self.logger = logger.getChild(f"okx_ws_{ws_type.value}")

        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to initialize OKXWebSocket",
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
            message = timestamp + 'GET' + '/users/self/verify'
            
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

            if self.testnet:
                auth_msg["args"][0]["x-simulated-trading"] = "1"

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
                    "timestamp": timestamp
                }
            )

        except WebSocketError:
            raise
        except Exception as e:
            raise AuthenticationError(
                "Failed to authenticate WebSocket",
                context={
                    "ws_type": self.ws_type,
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
        """Subscribe to a specific OKX channel."""
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

            # Build subscription message
            sub_msg = {
                "op": "subscribe",
                "args": [{
                    "channel": topic,
                    "instType": "SWAP"
                }]
            }

            await self.ws.send(json.dumps(sub_msg))
            self.state.subscribed_channels.add(topic)
            
            if "SWAP" not in self.state.instTypes:
                self.state.instTypes.append("SWAP")
            
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
            raise WebSocketError(
                "Failed to subscribe to topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a specific OKX channel."""
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
                "op": "unsubscribe",
                "args": [{
                    "channel": topic,
                    "instType": "SWAP"
                }]
            }
            
            await self.ws.send(json.dumps(unsub_msg))
            self.state.subscribed_channels.remove(topic)
            
            remaining_topics = len([t for t in self.state.subscribed_channels 
                                if "SWAP" in t])
            if remaining_topics == 0:
                self.state.instTypes.remove("SWAP")
            
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
            raise WebSocketError(
                "Failed to unsubscribe from topic",
                context={
                    "topic": topic,
                    "error": str(e)
                }
            )

    def _handle_public_message(self, message: Dict) -> None:
        """Process public endpoint message."""
        channel = message.get("arg", {}).get("channel", "")
        
        # Handle orderbook messages
        if channel.startswith("books"):
            checksum = message.get("data", [{}])[0].get("checksum")
            if checksum:
                if not self.state.sequence_number:
                    self.state.sequence_number = checksum
                    self.state.order_book_synced = True
                elif checksum <= self.state.sequence_number:
                    return
                self.state.sequence_number = checksum
        
        # Update last message time
        self.state.last_message = datetime.utcnow()

    def _handle_private_message(self, message: Dict) -> None:
        """Process private endpoint message."""
        # Track authenticated state
        if message.get("event") == "login":
            self.state.authenticated = True
            self.state.login_time = datetime.utcnow()
        
        # Update last message time
        self.state.last_message = datetime.utcnow()

    async def process_message(self, message: Dict) -> None:
        """Process incoming OKX WebSocket message."""
        try:
            # Handle events (error, login, etc.)
            if "event" in message:
                event_type = message["event"]
                
                if event_type == "error":
                    await handle_api_error(
                        error=WebSocketError(
                            message.get("msg", "Unknown error"),
                            context=message
                        ),
                        context={"message": message},
                        log_message="WebSocket error event received"
                    )
                    return
                    
                elif event_type == "login":
                    self.state.authenticated = True
                    self.state.login_time = datetime.utcnow()
                    self.logger.info(
                        "Successfully authenticated",
                        extra={
                            "attempts": self.state.auth_attempts,
                            "login_time": self.state.login_time.isoformat()
                        }
                    )
                    return
                    
                elif event_type == "pong":
                    self.state.last_pong = datetime.utcnow()
                    return

            # Handle data messages based on endpoint type
            if "data" in message and "arg" in message:
                if self.ws_type == WebSocketType.PUBLIC:
                    self._handle_public_message(message)
                else:
                    self._handle_private_message(message)
                    
                # Route to callbacks if registered
                channel = message["arg"].get("channel")
                if channel and channel in self.callbacks:
                    try:
                        await self.callbacks[channel](message["data"])
                    except Exception as e:
                        await handle_api_error(
                            error=e,
                            context={
                                "channel": channel,
                                "message_type": "data"
                            },
                            log_message="Callback processing failed"
                        )

        except ValidationError:
            raise
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
            self.logger.info("Closed OKX WebSocket connection")
                
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
            "instrument_types": self.state.instTypes,
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