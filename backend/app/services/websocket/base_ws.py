"""
Base WebSocket class providing core functionality for exchange connections.

Features:
- Enhanced error handling and recovery
- Comprehensive logging with context
- Message queue management
- Rate limiting
- Connection state tracking
- Resource cleanup
"""

from typing import Dict, Set, Optional, Any, Type, Callable, Awaitable, Protocol, List
import asyncio
import json
from datetime import datetime, timedelta
import websockets
from enum import Enum
from decimal import Decimal

class WebSocketState:
    """WebSocket connection state tracking."""
    
    def __init__(self):
        """Initialize WebSocket state."""
        self.connected = False
        self.connecting = False
        self.authenticated = False
        self.auth_attempts = 0
        self.subscribed_channels = set()
        self.last_message = None
        self.last_ping = None
        self.last_pong = None
        self.connection_attempts = 0
        self.error_count = 0
        self.error_timestamps = []
        self.message_rate = 0
        self.connection_id = datetime.utcnow().isoformat()

    def update_error_count(self) -> None:
        """Update error tracking with rate limiting."""
        now = datetime.utcnow()
        self.error_timestamps = [t for t in self.error_timestamps 
                               if now - t < timedelta(hours=1)]
        self.error_timestamps.append(now)
        self.error_count = len(self.error_timestamps)

    def reset_errors(self) -> None:
        """Reset error tracking."""
        self.error_count = 0
        self.error_timestamps.clear()

class BaseWebSocket:
    """Base WebSocket client providing core functionality."""
    
    def __init__(
        self,
        url: str,
        ping_interval: int = 20,
        ping_timeout: int = 10,
        reconnect_delay: int = 5,
        max_queue_size: int = 1000
    ):
        """Initialize WebSocket client."""
        try:
            if not url.startswith(("ws://", "wss://")):
                raise ValidationError(
                    "Invalid WebSocket URL",
                    context={"url": url}
                )

            self.url = url
            self.ping_interval = ping_interval
            self.ping_timeout = ping_timeout
            self.reconnect_delay = reconnect_delay
            
            self.state = WebSocketState()
            self.callbacks: Dict[str, Callable] = {}
            self.ws: Optional[websockets.WebSocketClientProtocol] = None
            self._stop = False
            
            self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
            self._message_processor: Optional[asyncio.Task] = None
            self._heartbeat_task: Optional[asyncio.Task] = None
            
            self.logger = logger.getChild(self.__class__.__name__)

        except ValidationError as e:
            self.logger.error(
                "WebSocket initialization failed",
                extra={"url": url, "error": str(e)}
            )
            raise
        except Exception as e:
            self.logger.error(
                "WebSocket initialization failed",
                extra={"url": url, "error": str(e)}
            )
            raise WebSocketError(
                "Failed to initialize WebSocket",
                context={"url": url, "error": str(e)}
            )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self) -> None:
        """Establish WebSocket connection with error handling."""
        if self.state.connecting:
            return

        try:
            self.state.connecting = True
            self.state.connection_attempts += 1

            self.ws = await websockets.connect(
                self.url,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout
            )

            self.state.connected = True
            self.state.last_ping = datetime.utcnow()
            self.state.reset_errors()

            self._start_tasks()

            self.logger.info(
                "Connected to WebSocket",
                extra={
                    "url": self.url,
                    "attempt": self.state.connection_attempts,
                    "connection_id": self.state.connection_id
                }
            )

        except websockets.exceptions.InvalidStatusCode as e:
            self.logger.error(
                "Invalid WebSocket status",
                extra={
                    "url": self.url,
                    "status_code": e.status_code,
                    "attempt": self.state.connection_attempts
                }
            )
            raise WebSocketError(
                "Invalid WebSocket status",
                context={
                    "url": self.url,
                    "status_code": e.status_code,
                    "attempt": self.state.connection_attempts
                }
            )
        except Exception as e:
            self.logger.error(
                "Connection failed",
                extra={
                    "url": self.url,
                    "attempt": self.state.connection_attempts,
                    "error": str(e)
                }
            )
            raise WebSocketError(
                "Failed to establish WebSocket connection",
                context={
                    "url": self.url,
                    "attempt": self.state.connection_attempts,
                    "error": str(e)
                }
            )
        finally:
            self.state.connecting = False

    async def close(self) -> None:
        """Close WebSocket connection and cleanup resources."""
        try:
            self._stop = True
            
            if self._message_processor:
                self._message_processor.cancel()
                try:
                    await self._message_processor
                except asyncio.CancelledError:
                    pass
                
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            
            if self.ws:
                try:
                    await self.ws.close()
                except Exception as e:
                    self.logger.error(
                        "Error closing WebSocket",
                        extra={"error": str(e)}
                    )
            
            self.state = WebSocketState()
            self.callbacks.clear()
            
            while not self.message_queue.empty():
                try:
                    self.message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                    
            self.logger.info("WebSocket closed and cleaned up")
            
        except Exception as e:
            self.logger.error(
                "Error during WebSocket cleanup",
                extra={"error": str(e)}
            )
            raise WebSocketError(
                "Failed to close WebSocket",
                context={"error": str(e)}
            )

    def _start_tasks(self) -> None:
        """Start background processing tasks."""
        if not self._message_processor:
            self._message_processor = asyncio.create_task(self._process_message_queue())

        if not self._heartbeat_task:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        max_delay = 300  # Maximum 5 minutes between retries
        
        while not self._stop and not self.state.connected:
            try:
                delay = min(
                    self.reconnect_delay * (2 ** (self.state.connection_attempts - 1)),
                    max_delay
                )
                
                self.logger.info(
                    "Attempting reconnection",
                    extra={
                        "attempt": self.state.connection_attempts,
                        "delay": delay
                    }
                )
                
                await asyncio.sleep(delay)
                await self.connect()
                
                if self.state.connected:
                    await self._resubscribe()
                    self.logger.info("Reconnection successful")
                    break

            except Exception as e:
                self.state.update_error_count()
                self.logger.error(
                    "Reconnection failed",
                    extra={
                        "attempt": self.state.connection_attempts,
                        "error_count": self.state.error_count,
                        "error": str(e)
                    }
                )
                if self.state.error_count >= 5:
                    raise WebSocketError(
                        "Maximum reconnection attempts reached",
                        context={
                            "attempts": self.state.connection_attempts,
                            "errors": self.state.error_count,
                            "error": str(e)
                        }
                    )
                await asyncio.sleep(self.reconnect_delay)

    async def _process_message_queue(self) -> None:
        """Process messages from queue with rate limiting."""
        while not self._stop:
            try:
                message = await self.message_queue.get()
                await self._handle_rate_limit()
                
                try:
                    await self.process_message(message)
                except Exception as e:
                    self.logger.error(
                        "Message processing failed",
                        extra={
                            "message_type": message.get("type"),
                            "error": str(e)
                        }
                    )
                    raise WebSocketError(
                        "Failed to process message",
                        context={
                            "message_type": message.get("type"),
                            "error": str(e)
                        }
                    )
                    
                self.message_queue.task_done()
                self.state.last_message = datetime.utcnow()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    "Message processor error",
                    extra={"error": str(e)}
                )
                await asyncio.sleep(1)

    async def _heartbeat_loop(self) -> None:
        """Maintain WebSocket connection with heartbeats."""
        while not self._stop:
            try:
                if not self.state.connected:
                    await asyncio.sleep(1)
                    continue

                now = datetime.utcnow()
                
                if (self.state.last_ping and self.state.last_pong and 
                    (now - self.state.last_pong).total_seconds() > self.ping_timeout):
                    self.logger.warning("Missed heartbeat response")
                    await self.reconnect()
                    continue

                if (not self.state.last_ping or 
                    (now - self.state.last_ping).total_seconds() > self.ping_interval):
                    await self.ws.ping()
                    self.state.last_ping = now

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    "Heartbeat loop error",
                    extra={"error": str(e)}
                )
                await asyncio.sleep(1)

    async def _handle_rate_limit(self) -> None:
        """Enforce rate limiting with exponential backoff."""
        if self.state.message_rate >= settings.RATE_LIMIT_ORDERS_PER_SECOND:
            retry_delay = self.reconnect_delay * (2 ** self.state.error_count)
            await asyncio.sleep(retry_delay)
            
            if self.state.message_rate >= settings.RATE_LIMIT_ORDERS_PER_SECOND:
                self.logger.error(
                    "Rate limit exceeded",
                    extra={
                        "rate": self.state.message_rate,
                        "limit": settings.RATE_LIMIT_ORDERS_PER_SECOND
                    }
                )
                raise RateLimitError(
                    "WebSocket message rate limit exceeded",
                    context={
                        "rate": self.state.message_rate,
                        "limit": settings.RATE_LIMIT_ORDERS_PER_SECOND
                    }
                )

    async def _resubscribe(self) -> None:
        """Resubscribe to channels after reconnection."""
        channels = self.state.subscribed_channels.copy()
        
        for channel in channels:
            try:
                await self.subscribe(channel)
                self.logger.info(
                    "Resubscribed to channel",
                    extra={"channel": channel}
                )
            except Exception as e:
                self.logger.error(
                    "Resubscription failed",
                    extra={
                        "channel": channel,
                        "error": str(e)
                    }
                )
                raise WebSocketError(
                    "Failed to resubscribe to channel",
                    context={
                        "channel": channel,
                        "error": str(e)
                    }
                )

    def add_callback(
        self,
        topic: str,
        callback: Callable[[Dict], Awaitable[None]]
    ) -> None:
        """Add a callback for a specific topic."""
        self.callbacks[topic] = callback
        self.logger.debug(
            "Added message callback",
            extra={
                "topic": topic,
                "callback": callback.__name__
            }
        )

    @staticmethod
    def generate_subscription_id() -> str:
        """Generate a unique subscription ID."""
        return f"sub_{datetime.utcnow().timestamp()}"

    async def is_healthy(self) -> bool:
        """Check if WebSocket connection is healthy."""
        if not self.state.connected:
            return False
            
        if not self.state.last_message:
            return False

        if (datetime.utcnow() - self.state.last_message) > timedelta(minutes=1):
            return False

        if self.state.last_ping and self.state.last_pong:
            heartbeat_diff = (self.state.last_pong - self.state.last_ping).total_seconds()
            if heartbeat_diff > self.ping_timeout:
                return False

        return True

    async def get_status(self) -> Dict:
        """Get detailed connection status information."""
        status = {
            "connected": self.state.connected,
            "connecting": self.state.connecting,
            "authenticated": self.state.authenticated,
            "auth_attempts": self.state.auth_attempts,
            "connection_attempts": self.state.connection_attempts,
            "error_count": self.state.error_count,
            "subscribed_channels": list(self.state.subscribed_channels),
            "message_rate": self.state.message_rate,
            "connection_id": self.state.connection_id,
            "last_message": self.state.last_message.isoformat() if self.state.last_message else None,
            "last_ping": self.state.last_ping.isoformat() if self.state.last_ping else None,
            "last_pong": self.state.last_pong.isoformat() if self.state.last_pong else None
        }

        if self.state.error_timestamps:
            status["error_timing"] = {
                "first_error": self.state.error_timestamps[0].isoformat(),
                "last_error": self.state.error_timestamps[-1].isoformat(),
                "error_count_last_hour": len(self.state.error_timestamps)
            }

        return status

    async def verify_connection(self) -> bool:
        """Verify WebSocket connection health and attempt recovery."""
        try:
            if not self.state.connected:
                await self.reconnect()
                return self.state.connected

            if self.ws and not self.ws.closed:
                try:
                    pong_waiter = await self.ws.ping()
                    await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout)
                    self.state.last_ping = datetime.utcnow()
                    return True
                except Exception:
                    self.logger.warning("Connection verification failed")
                    await self.reconnect()
                    return self.state.connected

            return False

        except Exception as e:
            self.logger.error(
                "Connection verification error",
                extra={"error": str(e)}
            )
            raise WebSocketError(
                "Failed to verify connection",
                context={"error": str(e)}
            )

    # Abstract methods to be implemented by subclasses
    async def subscribe(self, topic: str) -> None:
        raise NotImplementedError

    async def unsubscribe(self, topic: str) -> None:
        raise NotImplementedError

    async def process_message(self, message: Dict) -> None:
        raise NotImplementedError

# Import at end to avoid circular dependencies
from app.core.errors.base import WebSocketError, RateLimitError, ValidationError
from app.core.references import WebSocketType
from app.core.logging.logger import get_logger
from app.core.config.settings import settings

logger = get_logger(__name__)