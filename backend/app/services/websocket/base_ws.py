"""
Base WebSocket class providing core functionality for exchange connections.

Features:
- Enhanced error handling via decorators for high‑level operations.
- Message queue management
- Rate limiting
- Connection state tracking
- Resource cleanup
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Set, Optional, Callable, Awaitable

import asyncio
import websockets

from app.core.errors.base import WebSocketError, RateLimitError, ValidationError
from app.core.config.settings import settings
from app.core.logging.logger import get_logger
from app.core.errors.decorators import error_handler

logger = get_logger(__name__)


class WebSocketState:
    """Tracks the state of a WebSocket connection."""
    def __init__(self) -> None:
        self.connected: bool = False
        self.connecting: bool = False
        self.authenticated: bool = False
        self.auth_attempts: int = 0
        self.subscribed_channels: Set[str] = set()
        self.last_message: Optional[datetime] = None
        self.last_ping: Optional[datetime] = None
        self.last_pong: Optional[datetime] = None
        self.connection_attempts: int = 0
        self.error_count: int = 0
        self.error_timestamps: list[datetime] = []
        self.message_rate: int = 0
        self.connection_id: str = datetime.utcnow().isoformat()

    def update_error_count(self) -> None:
        now = datetime.utcnow()
        self.error_timestamps = [t for t in self.error_timestamps if now - t < timedelta(hours=1)]
        self.error_timestamps.append(now)
        self.error_count = len(self.error_timestamps)

    def reset_errors(self) -> None:
        self.error_count = 0
        self.error_timestamps.clear()


class BaseWebSocket(ABC):
    """
    Abstract Base WebSocket client providing core functionality.
    """
    def __init__(
        self,
        url: str,
        ping_interval: int = 20,
        ping_timeout: int = 10,
        reconnect_delay: int = 5,
        max_queue_size: int = 1000
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        if not url.startswith(("ws://", "wss://")):
            error_msg = f"Invalid WebSocket URL: {url}"
            self.logger.error(error_msg, extra={"url": url})
            raise ValidationError(error_msg, context={"url": url})
        self.url = url
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.reconnect_delay = reconnect_delay
        self._stop: bool = False
        self.message_queue: asyncio.Queue[Dict] = asyncio.Queue(maxsize=max_queue_size)
        self._message_processor: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.state = WebSocketState()
        self.callbacks: Dict[str, Callable[[Dict], Awaitable[None]]] = {}

    async def __aenter__(self) -> "BaseWebSocket":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    @abstractmethod
    async def subscribe(self, topic: str) -> None:
        """Subscribe to a specific topic."""
        pass

    @abstractmethod
    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a specific topic."""
        pass

    @abstractmethod
    async def process_message(self, message: Dict) -> None:
        """Process an incoming message."""
        pass

    @error_handler(
        context_extractor=lambda self: {"url": self.url, "attempt": self.state.connection_attempts},
        log_message="Failed to connect to WebSocket"
    )
    async def connect(self) -> None:
        """Establish WebSocket connection with error handling."""
        self.state.connecting = True
        self.state.connection_attempts += 1
        try:
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
                extra={"url": self.url, "attempt": self.state.connection_attempts, "connection_id": self.state.connection_id}
            )
        except websockets.exceptions.InvalidStatusCode as e:
            self.logger.error(
                "Invalid WebSocket status",
                extra={"url": self.url, "status_code": e.status_code, "attempt": self.state.connection_attempts}
            )
            raise WebSocketError("Invalid WebSocket status", context={"url": self.url, "status_code": e.status_code, "attempt": self.state.connection_attempts})
        except Exception as e:
            self.logger.error(
                "Connection failed",
                extra={"url": self.url, "attempt": self.state.connection_attempts, "error": str(e)}
            )
            raise WebSocketError("Failed to establish WebSocket connection", context={"url": self.url, "attempt": self.state.connection_attempts, "error": str(e)})
        finally:
            self.state.connecting = False

    @error_handler(
        log_message="Failed to close WebSocket connection"
    )
    async def close(self) -> None:
        """Close WebSocket connection and clean up resources."""
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
        if hasattr(self, 'ws') and self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                self.logger.error("Error closing WebSocket", extra={"error": str(e)})
        self.state = WebSocketState()
        self.callbacks.clear()
        while not self.message_queue.empty():
            try:
                self.message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.logger.info("WebSocket closed and cleaned up")

    def _start_tasks(self) -> None:
        """Start background processing tasks if not already running."""
        if not self._message_processor:
            self._message_processor = asyncio.create_task(self._process_message_queue())
        if not self._heartbeat_task:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    @error_handler(
        context_extractor=lambda self: {"attempts": self.state.connection_attempts, "error_count": self.state.error_count},
        log_message="Reconnection failed"
    )
    async def reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        max_delay = 300  # Maximum 5 minutes delay
        while not self._stop and not self.state.connected:
            try:
                delay = min(self.reconnect_delay * (2 ** (self.state.connection_attempts - 1)), max_delay)
                self.logger.info("Attempting reconnection", extra={"attempt": self.state.connection_attempts, "delay": delay})
                await asyncio.sleep(delay)
                await self.connect()
                if self.state.connected:
                    await self._resubscribe()
                    self.logger.info("Reconnection successful")
                    break
            except Exception as e:
                self.state.update_error_count()
                self.logger.error("Reconnection attempt failed", extra={"attempt": self.state.connection_attempts, "error_count": self.state.error_count, "error": str(e)})
                if self.state.error_count >= 5:
                    raise WebSocketError("Maximum reconnection attempts reached", context={"attempts": self.state.connection_attempts, "error_count": self.state.error_count, "error": str(e)})
                await asyncio.sleep(self.reconnect_delay)

    async def _process_message_queue(self) -> None:
        """
        Process messages from the queue with inline error handling.
        This loop is kept with inline try/except to properly handle cancellation and per‑message errors.
        """
        while not self._stop:
            try:
                message = await self.message_queue.get()
                await self._handle_rate_limit()
                try:
                    await self.process_message(message)
                except Exception as e:
                    self.logger.error("Message processing failed", extra={"message_type": message.get("type"), "error": str(e)})
                    raise WebSocketError("Failed to process message", context={"message_type": message.get("type"), "error": str(e)})
                self.message_queue.task_done()
                self.state.last_message = datetime.utcnow()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Message processor error", extra={"error": str(e)})
                await asyncio.sleep(1)

    async def _heartbeat_loop(self) -> None:
        """
        Maintain WebSocket connection with periodic heartbeats.
        Inline try/except blocks remain here to properly manage loop cancellation and recovery.
        """
        while not self._stop:
            try:
                if not self.state.connected:
                    await asyncio.sleep(1)
                    continue
                now = datetime.utcnow()
                if self.state.last_message and (now - self.state.last_message).total_seconds() > 60:
                    await self.reconnect()
                    continue
                if self.state.last_ping and self.state.last_pong and (now - self.state.last_pong).total_seconds() > self.ping_timeout:
                    self.logger.warning("Missed heartbeat response")
                    await self.reconnect()
                    continue
                if not self.state.last_ping or (now - self.state.last_ping).total_seconds() > self.ping_interval:
                    await self.ws.ping()  # type: ignore
                    self.state.last_ping = now
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Heartbeat loop error", extra={"error": str(e)})
                await asyncio.sleep(1)

    @error_handler(
        context_extractor=lambda self: {"last_ping": self.state.last_ping.isoformat() if self.state.last_ping else None},
        log_message="Rate limit handling failed"
    )
    async def _handle_rate_limit(self) -> None:
        """
        Enforce rate limiting with exponential backoff.
        """
        min_interval = 1.0 / settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND
        now = datetime.now().timestamp()
        elapsed = now - (self.state.last_ping.timestamp() if self.state.last_ping else now)
        if elapsed < min_interval:
            for retry in range(3):
                wait_time = (min_interval - elapsed) * (2 ** retry)
                await asyncio.sleep(wait_time)
                now = datetime.now().timestamp()
                elapsed = now - (self.state.last_ping.timestamp() if self.state.last_ping else now)
                if elapsed >= min_interval:
                    break
            else:
                raise RateLimitError("Rate limit exceeded", context={"rate_limit": settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND, "elapsed": elapsed})
        self.state.last_ping = datetime.now()

    @error_handler(
        context_extractor=lambda self: {"subscribed_channels": list(self.state.subscribed_channels)},
        log_message="Resubscription failed"
    )
    async def _resubscribe(self) -> None:
        """
        Re-subscribe to previously subscribed channels after reconnection.
        """
        channels = self.state.subscribed_channels.copy()
        for channel in channels:
            try:
                await self.subscribe(channel)
                self.logger.info("Resubscribed to channel", extra={"channel": channel})
            except Exception as e:
                self.logger.error("Resubscription failed", extra={"channel": channel, "error": str(e)})
                raise WebSocketError("Failed to resubscribe to channel", context={"channel": channel, "error": str(e)})

    @error_handler(
        context_extractor=lambda self: {"last_ping": self.state.last_ping.isoformat() if self.state.last_ping else None},
        log_message="Connection verification failed"
    )
    async def verify_connection(self) -> Dict:
        """
        Verify WebSocket connection health and attempt recovery.
        """
        try:
            self.state.last_ping = self.state.last_ping or datetime.utcnow()
            if not self.state.connected:
                await self.reconnect()
                return {"status": "reconnecting"}
            if self.ws and not self.ws.closed:
                pong_waiter = await self.ws.ping()  # type: ignore
                await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout)
                self.state.last_ping = datetime.utcnow()
                return {"status": "connected"}
            return {"status": "disconnected"}
        except Exception as e:
            self.logger.error("Connection verification error", extra={"error": str(e)})
            raise WebSocketError("Failed to verify connection", context={"error": str(e)})
