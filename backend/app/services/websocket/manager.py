"""
Enhanced WebSocket manager with optimized resource management.

Features:
- Connection pooling and lifecycle management
- State tracking and health monitoring 
- Resource cleanup and logging
"""

from typing import Dict, Optional, Any, Set, List, Type, Callable, Awaitable
import asyncio
from datetime import datetime, timedelta

from app.core.references import ConnectionState, WebSocketType

class ConnectionInfo:
    """Connection information and state tracking."""
    
    def __init__(
        self,
        client: 'WebSocketClientProtocol',
        connection_type: str,
        creation_time: datetime
    ):
        self.client = client
        self.connection_type = connection_type
        self.creation_time = creation_time
        self.state = ConnectionState.CONNECTING
        self.last_message = None
        self.error_count = 0
        self.reconnect_attempts = 0
        self.subscriptions: Set[str] = set()
        self.message_handlers: Dict[str, Callable] = {}
        self.lock = asyncio.Lock()

class WebSocketManager:
    """Centralized WebSocket connection manager."""

    def __init__(self):
        self._connections: Dict[str, ConnectionInfo] = {}
        self._maintenance_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # Configuration 
        self._maintenance_interval = 30  # seconds
        self._max_reconnect_attempts = 3
        self._reconnect_delay = 5  # seconds
        self._connection_timeout = 30  # seconds
        
        self.logger = logger.getChild('websocket_manager')

    async def start(self) -> None:
        """Start maintenance task."""
        try:
            if not self._maintenance_task:
                self._maintenance_task = asyncio.create_task(self._maintenance_loop())
            self.logger.info("WebSocket manager started")
        except Exception as e:
            raise ServiceError(
                "Failed to start WebSocket manager",
                context={"error": str(e)}
            )

    async def stop(self) -> None:
        """Stop maintenance and cleanup connections."""
        try:
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
        except Exception as e:
            self.logger.error(
                "Error stopping WebSocket manager",
                extra={"error": str(e)}
            )

    async def create_connection(
        self,
        connection_id: str,
        client: 'WebSocketClientProtocol',
        connection_type: str
    ) -> None:
        """Create and initialize WebSocket connection."""
        try:
            async with self._lock:
                if connection_id in self._connections:
                    raise ValidationError(
                        "Connection already exists",
                        context={"connection_id": connection_id}
                    )

                info = ConnectionInfo(
                    client=client,
                    connection_type=connection_type,
                    creation_time=datetime.utcnow()
                )
                self._connections[connection_id] = info

                try:
                    await client.connect()
                    info.state = ConnectionState.CONNECTED
                    self.logger.info(
                        "Created WebSocket connection",
                        extra={
                            "connection_id": connection_id,
                            "type": connection_type
                        }
                    )
                except Exception as e:
                    del self._connections[connection_id]
                    raise WebSocketError(
                        "Failed to establish connection",
                        context={
                            "connection_id": connection_id,
                            "type": connection_type,
                            "error": str(e)
                        }
                    )
        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to create connection",
                context={
                    "connection_id": connection_id,
                    "error": str(e)
                }
            )

    async def close_connection(self, connection_id: str) -> None:
        """Close WebSocket connection and cleanup."""
        try:
            async with self._lock:
                if connection_id not in self._connections:
                    return

                info = self._connections[connection_id]
                info.state = ConnectionState.DISCONNECTING

                try:
                    await info.client.close()
                except Exception as e:
                    self.logger.error(
                        "Error closing connection",
                        extra={
                            "connection_id": connection_id,
                            "error": str(e)
                        }
                    )

                del self._connections[connection_id]
                self.logger.info(
                    "Closed WebSocket connection",
                    extra={"connection_id": connection_id}
                )
        except Exception as e:
            self.logger.error(
                "Error during connection cleanup",
                extra={
                    "connection_id": connection_id,
                    "error": str(e)
                }
            )

    async def get_connection(
        self,
        connection_id: str
    ) -> Optional['WebSocketClientProtocol']:
        """Get WebSocket client if exists."""
        if info := self._connections.get(connection_id):
            return info.client
        return None

    async def verify_connections(
        self,
        connection_id: Optional[str] = None
    ) -> Dict[str, str]:
        """Verify connection(s) health."""
        states = {}
        connections = (
            {connection_id: self._connections[connection_id]}
            if connection_id else self._connections
        )
        
        for conn_id, info in connections.items():
            try:
                is_healthy = await info.client.is_healthy()
                states[conn_id] = (
                    info.state.value if is_healthy
                    else ConnectionState.ERROR.value
                )
                
                if not is_healthy:
                    await self._attempt_reconnect(conn_id)
                    
            except Exception as e:
                self.logger.error(
                    "Connection verification failed",
                    extra={
                        "connection_id": conn_id,
                        "error": str(e)
                    }
                )
                states[conn_id] = ConnectionState.ERROR.value
                
        return states

    async def subscribe(
        self,
        connection_id: str,
        topic: str,
        handler: Optional[Callable] = None
    ) -> None:
        """Subscribe to topic with optional handler."""
        try:
            if info := self._connections.get(connection_id):
                async with info.lock:
                    await info.client.subscribe(topic)
                    info.subscriptions.add(topic)
                    if handler:
                        info.message_handlers[topic] = handler
                    self.logger.info(
                        "Subscribed to topic",
                        extra={
                            "connection_id": connection_id,
                            "topic": topic
                        }
                    )
            else:
                raise ValidationError(
                    "Connection not found",
                    context={"connection_id": connection_id}
                )
        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to subscribe",
                context={
                    "connection_id": connection_id,
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def unsubscribe(
        self,
        connection_id: str,
        topic: str
    ) -> None:
        """Unsubscribe from topic."""
        try:
            if info := self._connections.get(connection_id):
                async with info.lock:
                    await info.client.unsubscribe(topic)
                    info.subscriptions.discard(topic)
                    info.message_handlers.pop(topic, None)
                    self.logger.info(
                        "Unsubscribed from topic",
                        extra={
                            "connection_id": connection_id,
                            "topic": topic
                        }
                    )
            else:
                raise ValidationError(
                    "Connection not found",
                    context={"connection_id": connection_id}
                )
        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to unsubscribe",
                context={
                    "connection_id": connection_id,
                    "topic": topic,
                    "error": str(e)
                }
            )

    async def sync_order_book(
        self,
        connection_id: str,
        symbol: str,
        data: Dict[str, Any],
        is_snapshot: bool = True
    ) -> None:
        """Sync order book data."""
        try:
            if info := self._connections.get(connection_id):
                async with info.lock:
                    if is_snapshot:
                        await info.client.handle_snapshot(symbol, data)
                    else:
                        await info.client.handle_delta(symbol, data)
                    self.logger.info(
                        "Synced order book",
                        extra={
                            "connection_id": connection_id,
                            "symbol": symbol,
                            "type": "snapshot" if is_snapshot else "delta"
                        }
                    )
            else:
                raise ValidationError(
                    "Connection not found",
                    context={"connection_id": connection_id}
                )
        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "Failed to sync order book",
                context={
                    "connection_id": connection_id,
                    "symbol": symbol,
                    "error": str(e)
                }
            )

    async def get_connection_status(
        self,
        connection_id: str
    ) -> Dict[str, Any]:
        """Get detailed connection status."""
        if info := self._connections.get(connection_id):
            return {
                "state": info.state.value,
                "connection_type": info.connection_type,
                "creation_time": info.creation_time.isoformat(),
                "last_message": info.last_message.isoformat() if info.last_message else None,
                "error_count": info.error_count,
                "reconnect_attempts": info.reconnect_attempts,
                "subscriptions": list(info.subscriptions),
                "active_handlers": list(info.message_handlers.keys())
            }
        return {
            "state": ConnectionState.DISCONNECTED.value,
            "error": "Connection not found"
        }

    async def _attempt_reconnect(
        self,
        connection_id: str
    ) -> None:
        """Attempt connection reconnection."""
        if info := self._connections.get(connection_id):
            info.state = ConnectionState.RECONNECTING
            info.reconnect_attempts += 1

            try:
                try:
                    await info.client.close()
                except Exception:
                    pass

                await info.client.connect()
                info.state = ConnectionState.CONNECTED

                subscriptions = info.subscriptions.copy()
                for topic in subscriptions:
                    try:
                        await info.client.subscribe(topic)
                    except Exception as e:
                        self.logger.error(
                            "Failed to resubscribe",
                            extra={
                                "connection_id": connection_id,
                                "topic": topic,
                                "error": str(e)
                            }
                        )

                self.logger.info(
                    "Successfully reconnected",
                    extra={
                        "connection_id": connection_id,
                        "attempt": info.reconnect_attempts
                    }
                )

            except Exception as e:
                info.error_count += 1
                info.state = ConnectionState.ERROR
                
                if info.reconnect_attempts >= self._max_reconnect_attempts:
                    raise WebSocketError(
                        "Maximum reconnection attempts reached",
                        context={
                            "connection_id": connection_id,
                            "attempts": info.reconnect_attempts,
                            "error": str(e)
                        }
                    )

                self.logger.error(
                    "Reconnection attempt failed",
                    extra={
                        "connection_id": connection_id,
                        "attempt": info.reconnect_attempts,
                        "error": str(e)
                    }
                )
                await asyncio.sleep(
                    self._reconnect_delay * (2 ** (info.reconnect_attempts - 1))
                )

    async def _maintenance_loop(self) -> None:
        """Connection maintenance and cleanup loop."""
        while True:
            try:
                await asyncio.sleep(self._maintenance_interval)
                
                async with self._lock:
                    now = datetime.utcnow()
                    to_close = []

                    for connection_id, info in self._connections.items():
                        # Check stale connections
                        if (now - info.creation_time).total_seconds() > 86400:
                            to_close.append(connection_id)
                            continue

                        # Check error state
                        if info.state == ConnectionState.ERROR:
                            to_close.append(connection_id)
                            continue

                        # Check message timeout
                        if (info.last_message and 
                            (now - info.last_message).total_seconds() > 300):
                            to_close.append(connection_id)
                            continue

                        # Verify health if connected
                        if info.state == ConnectionState.CONNECTED:
                            try:
                                if not await info.client.is_healthy():
                                    await self._attempt_reconnect(connection_id)
                            except Exception as e:
                                self.logger.error(
                                    "Health check failed",
                                    extra={
                                        "connection_id": connection_id,
                                        "error": str(e)
                                    }
                                )
                                await self._attempt_reconnect(connection_id)

                    # Close stale connections
                    for connection_id in to_close:
                        try:
                            await self.close_connection(connection_id)
                        except Exception as e:
                            self.logger.error(
                                "Failed to close stale connection",
                                extra={
                                    "connection_id": connection_id,
                                    "error": str(e)
                                }
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    "Error in maintenance loop",
                    extra={"error": str(e)}
                )
                await asyncio.sleep(5)

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    WebSocketError,
    ValidationError,
    ServiceError
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

# Create global instance
ws_manager = WebSocketManager()