"""
WebSocket Manager with Simplified Start/Stop and Centralized Connection Management

Features:
- Connection pooling and lifecycle management
- State tracking and health monitoring
- Simplified maintenance loop for reconnection and cleanup
- Designed for storing the manager instance centrally (e.g. in app.state)
"""

from typing import Dict, Optional, Any, Callable, Set
import asyncio
from datetime import datetime, timedelta

from app.core.references import ConnectionState
from app.core.errors.base import ValidationError, WebSocketError, ServiceError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class ConnectionInfo:
    """Holds a WebSocket connection along with its state and related metadata."""
    def __init__(self, client: Any, connection_type: str, creation_time: datetime):
        self.client = client
        self.connection_type = connection_type
        self.creation_time = creation_time
        self.state: ConnectionState = ConnectionState.CONNECTING
        self.last_message: Optional[datetime] = None
        self.error_count: int = 0
        self.reconnect_attempts: int = 0
        self.subscriptions: Set[str] = set()
        self.message_handlers: Dict[str, Callable] = {}
        self.lock = asyncio.Lock()


class WebSocketManager:
    """
    Centralized WebSocket connection manager.

    This manager handles connection creation, maintenance (via a background loop),
    health checking, reconnection, subscription management, and cleanup.
    """
    def __init__(self):
        self._connections: Dict[str, ConnectionInfo] = {}
        self._maintenance_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._maintenance_interval = 30  # seconds
        self._max_reconnect_attempts = 3
        self._reconnect_delay = 5  # seconds
        self._connection_timeout = 30  # seconds
        self.logger = get_logger("websocket_manager")

    @error_handler(
        context_extractor=lambda self: {},
        log_message="Failed to start WebSocket manager"
    )
    async def start(self) -> None:
        """
        Start the maintenance loop.

        You can call this method during application startup (e.g. in main.py)
        and store the manager instance centrally (e.g. in app.state).
        """
        if not self._maintenance_task:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        self.logger.info("WebSocket manager started")

    @error_handler(
        context_extractor=lambda self: {},
        log_message="Failed to stop WebSocket manager"
    )
    async def stop(self) -> None:
        """
        Stop the maintenance loop and close all connections.
        """
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for connection_id in list(self._connections.keys()):
                await self.close_connection(connection_id)
        self.logger.info("WebSocket manager stopped")

    @error_handler(
        context_extractor=lambda self, connection_id, client, connection_type: {"connection_id": connection_id, "connection_type": connection_type},
        log_message="Failed to create connection"
    )
    async def create_connection(self, connection_id: str, client: Any, connection_type: str) -> None:
        """
        Create and store a new WebSocket connection.
        """
        async with self._lock:
            if connection_id in self._connections:
                raise ValidationError("Connection already exists", context={"connection_id": connection_id})
            info = ConnectionInfo(client, connection_type, datetime.utcnow())
            self._connections[connection_id] = info
        try:
            await client.connect()
        except Exception as e:
            async with self._lock:
                self._connections.pop(connection_id, None)
            raise WebSocketError("Failed to establish connection", context={"connection_id": connection_id, "type": connection_type, "error": str(e)}) from e
        info.state = ConnectionState.CONNECTED
        self.logger.info("Created WebSocket connection", extra={"connection_id": connection_id, "type": connection_type})

    @error_handler(
        context_extractor=lambda self, connection_id: {"connection_id": connection_id},
        log_message="Failed to close connection"
    )
    async def close_connection(self, connection_id: str) -> None:
        """
        Close and remove the connection with the given ID.
        """
        async with self._lock:
            info = self._connections.pop(connection_id, None)
        if not info:
            return
        try:
            await info.client.close()
            info.state = ConnectionState.DISCONNECTED
        except Exception as e:
            self.logger.error("Error closing WebSocket connection", extra={"connection_id": connection_id, "error": str(e)})
        self.logger.info("Closed WebSocket connection", extra={"connection_id": connection_id})

    def get_connection(self, connection_id: str) -> Optional[Any]:
        """
        Return the client object for a given connection ID, or None if not found.
        """
        info = self._connections.get(connection_id)
        return info.client if info else None

    @error_handler(
        context_extractor=lambda self, connection_id=None: {"connection_id": connection_id} if connection_id else {},
        log_message="Failed to verify connections"
    )
    async def verify_connections(self, connection_id: Optional[str] = None) -> Dict[str, str]:
        """
        Verify the health of one or all connections.
        If a connection is unhealthy, attempt reconnection.
        """
        states = {}
        connections = {connection_id: self._connections[connection_id]} if connection_id else self._connections
        for conn_id, info in connections.items():
            try:
                is_healthy = await info.client.is_healthy()
                states[conn_id] = "CONNECTED" if is_healthy else "ERROR"
                if not is_healthy:
                    await self._attempt_reconnect(conn_id)
            except Exception as e:
                self.logger.error("Connection verification failed", extra={"connection_id": conn_id, "error": str(e)})
                states[conn_id] = "ERROR"
        return states

    @error_handler(
        context_extractor=lambda self, connection_id, topic, handler=None: {"connection_id": connection_id, "topic": topic},
        log_message="Failed to subscribe"
    )
    async def subscribe(self, connection_id: str, topic: str, handler: Optional[Callable] = None) -> None:
        """
        Subscribe to a topic on the connection.
        Optionally, register a callback handler.
        """
        info = self._get_connection_info(connection_id)
        async with info.lock:
            await info.client.subscribe(topic)
            info.subscriptions.add(topic)
            if handler:
                info.message_handlers[topic] = handler
        self.logger.info("Subscribed to topic", extra={"connection_id": connection_id, "topic": topic})

    @error_handler(
        context_extractor=lambda self, connection_id, topic: {"connection_id": connection_id, "topic": topic},
        log_message="Failed to unsubscribe"
    )
    async def unsubscribe(self, connection_id: str, topic: str) -> None:
        """
        Unsubscribe from a topic on the connection.
        """
        info = self._get_connection_info(connection_id)
        async with info.lock:
            await info.client.unsubscribe(topic)
            info.subscriptions.discard(topic)
            info.message_handlers.pop(topic, None)
        self.logger.info("Unsubscribed from topic", extra={"connection_id": connection_id, "topic": topic})

    @error_handler(
        context_extractor=lambda self, connection_id, symbol, data, is_snapshot=True: {"connection_id": connection_id, "symbol": symbol, "snapshot": is_snapshot},
        log_message="Failed to sync order book"
    )
    async def sync_order_book(self, connection_id: str, symbol: str, data: Dict[str, Any], is_snapshot: bool = True) -> None:
        """
        Synchronize the order book for a given symbol.
        """
        info = self._get_connection_info(connection_id)
        async with info.lock:
            if is_snapshot:
                await info.client.handle_snapshot(symbol, data)
            else:
                await info.client.handle_delta(symbol, data)
        self.logger.info("Synced order book", extra={"connection_id": connection_id, "symbol": symbol, "type": "snapshot" if is_snapshot else "delta"})

    @error_handler(
        context_extractor=lambda self, connection_id: {"connection_id": connection_id},
        log_message="Failed to attempt reconnection"
    )
    async def _attempt_reconnect(self, connection_id: str) -> None:
        """
        Attempt to reconnect the given connection, and resubscribe to its channels.
        """
        info = self._connections.get(connection_id)
        if not info:
            return
        info.state = ConnectionState.RECONNECTING
        info.reconnect_attempts += 1
        try:
            try:
                await info.client.close()
            except Exception:
                pass
            await info.client.connect()
            info.state = ConnectionState.CONNECTED
            # Resubscribe to previously subscribed topics.
            subscriptions = info.subscriptions.copy()
            for topic in subscriptions:
                try:
                    await info.client.subscribe(topic)
                except Exception as e:
                    self.logger.error("Failed to resubscribe", extra={"connection_id": connection_id, "topic": topic, "error": str(e)})
                    raise WebSocketError("Failed to resubscribe to channel", context={"channel": topic, "error": str(e)})
            self.logger.info("Successfully reconnected", extra={"connection_id": connection_id, "attempt": info.reconnect_attempts})
        except Exception as e:
            info.error_count += 1
            info.state = ConnectionState.ERROR
            if info.reconnect_attempts >= self._max_reconnect_attempts:
                raise WebSocketError("Maximum reconnection attempts reached", context={"connection_id": connection_id, "attempts": info.reconnect_attempts, "error": str(e)}) from e
            self.logger.error("Reconnection attempt failed", extra={"connection_id": connection_id, "attempt": info.reconnect_attempts, "error": str(e)})
            await asyncio.sleep(self._reconnect_delay * (2 ** (info.reconnect_attempts - 1)))

    def _get_connection_info(self, connection_id: str) -> ConnectionInfo:
        """Retrieve the ConnectionInfo object for a given connection ID."""
        if connection_id not in self._connections:
            raise ValidationError("Connection not found", context={"connection_id": connection_id})
        return self._connections[connection_id]

    async def _maintenance_loop(self) -> None:
        """
        Maintenance loop to check connection health and clean up stale connections.
        """
        while True:
            try:
                await asyncio.sleep(self._maintenance_interval)
                now = datetime.utcnow()
                to_close = []
                async with self._lock:
                    for connection_id, info in list(self._connections.items()):
                        # Close connections older than 24 hours.
                        if (now - info.creation_time).total_seconds() > 86400:
                            to_close.append(connection_id)
                            continue
                        if info.state == ConnectionState.ERROR:
                            to_close.append(connection_id)
                            continue
                        if info.last_message and (now - info.last_message).total_seconds() > 300:
                            to_close.append(connection_id)
                            continue
                        if info.state == ConnectionState.CONNECTED:
                            try:
                                if not await info.client.is_healthy():
                                    await self._attempt_reconnect(connection_id)
                            except Exception as e:
                                self.logger.error("Health check failed", extra={"connection_id": connection_id, "error": str(e)})
                                await self._attempt_reconnect(connection_id)
                for connection_id in to_close:
                    try:
                        await self.close_connection(connection_id)
                    except Exception as e:
                        self.logger.error("Failed to close stale connection", extra={"connection_id": connection_id, "error": str(e)})
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in maintenance loop", extra={"error": str(e)})
                await asyncio.sleep(5)

# Global instance of the WebSocket manager.
ws_manager = WebSocketManager()
