"""
WebSocket endpoints for real-time UI communication with enhanced error handling.

Features:
- WebSocket connection management for UI clients
- Bot subscription management and update broadcasting
- Basic health checking and heartbeat messages
- Minimal inline error handling (errors are logged and the connection is closed)
- Authentication is enforced via dependencies
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, Set, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status

from app.api.v1.deps import get_current_user
from app.core.logging.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class UIConnectionManager:
    """
    Manages WebSocket connections and bot subscriptions for UI clients.
    """
    HEALTH_CHECK_INTERVAL = 30  # seconds

    def __init__(self) -> None:
        self.connections: Dict[str, WebSocket] = {}
        self.bot_subscriptions: Dict[str, Set[str]] = {}
        self._health_check_tasks: Dict[str, asyncio.Task] = {}
        self._connection_metrics: Dict[str, Any] = {
            "total_connections": 0,
            "active_connections": 0,
            "error_count": 0,
        }

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[user_id] = websocket
        self._health_check_tasks[user_id] = asyncio.create_task(
            self._monitor_connection_health(user_id, websocket)
        )
        self._connection_metrics["total_connections"] += 1
        self._connection_metrics["active_connections"] += 1
        logger.info(
            f"WebSocket connection established for user {user_id}",
            extra={
                "user_id": user_id,
                "remote_ip": websocket.client.host if websocket.client else None,
                "metrics": self._connection_metrics,
            },
        )

    async def disconnect(self, user_id: str) -> None:
        task = self._health_check_tasks.pop(user_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        websocket = self.connections.pop(user_id, None)
        if websocket:
            await websocket.close()
            self._connection_metrics["active_connections"] = max(
                self._connection_metrics["active_connections"] - 1, 0
            )
        # Remove user from all bot subscriptions.
        for subscribers in self.bot_subscriptions.values():
            subscribers.discard(user_id)
        logger.info(
            f"WebSocket connection closed for user {user_id}",
            extra={"user_id": user_id, "metrics": self._connection_metrics},
        )

    async def _monitor_connection_health(self, user_id: str, websocket: WebSocket) -> None:
        while True:
            try:
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
                await websocket.send_json({"type": "ping"})
                # (Optionally, update health metrics here.)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "WebSocket health check failed",
                    extra={"user_id": user_id, "action": "health_check", "error": str(e)},
                )
                await self.disconnect(user_id)
                break

    async def subscribe_to_bot(self, user_id: str, bot_id: str) -> None:
        if not user_id or not bot_id:
            raise ValueError("Invalid subscription parameters: missing user_id or bot_id")
        subscribers = self.bot_subscriptions.setdefault(bot_id, set())
        subscribers.add(user_id)
        logger.info(
            f"Bot subscription added for user {user_id} to bot {bot_id}",
            extra={"user_id": user_id, "bot_id": bot_id, "total_subscriptions": len(subscribers)},
        )

    async def unsubscribe_from_bot(self, user_id: str, bot_id: str) -> None:
        if bot_id in self.bot_subscriptions:
            self.bot_subscriptions[bot_id].discard(user_id)
            logger.info(
                f"Bot subscription removed for user {user_id} from bot {bot_id}",
                extra={"user_id": user_id, "bot_id": bot_id, "total_subscriptions": len(self.bot_subscriptions[bot_id])},
            )

    async def broadcast_bot_update(self, bot_id: str, data: dict) -> None:
        subscribers = self.bot_subscriptions.get(bot_id)
        if not subscribers:
            return
        message = {
            "type": "bot_update",
            "bot_id": bot_id,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        failed_users = set()
        for user_id in subscribers.copy():
            websocket = self.connections.get(user_id)
            if websocket:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    failed_users.add(user_id)
                    logger.error(
                        "Failed to send bot update",
                        extra={"user_id": user_id, "bot_id": bot_id, "error": str(e)},
                    )
        for user_id in failed_users:
            await self.disconnect(user_id)


manager = UIConnectionManager()


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    current_user: Any = Depends(get_current_user),
):
    """
    WebSocket endpoint for UI connections.
    Validates that the user in the token matches the connection path, then processes incoming messages.
    """
    try:
        if str(current_user.id) != user_id:
            raise ValueError("User ID mismatch between token and connection path")
        await manager.connect(user_id, websocket)
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid message format: {raw_message}") from e
            if not isinstance(message, dict) or "type" not in message:
                raise ValueError(f"Invalid message structure: {message}")
            msg_type = message.get("type")
            if msg_type == "subscribe":
                bot_id = message.get("bot_id")
                if not bot_id:
                    raise ValueError("Missing bot_id in subscribe message")
                await manager.subscribe_to_bot(user_id, bot_id)
                try:
                    # Import here to avoid circular dependency issues.
                    from app.services.reference.manager import reference_manager
                    bot_state = await reference_manager.get_reference(
                        reference_id=bot_id, reference_type="Bot"
                    )
                    if bot_state:
                        await manager.broadcast_bot_update(bot_id, bot_state)
                except Exception as e:
                    logger.error(
                        f"Failed to send initial bot state for bot {bot_id}",
                        extra={"bot_id": bot_id, "error": str(e)},
                    )
            elif msg_type == "unsubscribe":
                bot_id = message.get("bot_id")
                if not bot_id:
                    raise ValueError("Missing bot_id in unsubscribe message")
                await manager.unsubscribe_from_bot(user_id, bot_id)
            elif msg_type == "pong":
                continue  # Pong messages serve as heartbeat responses.
            else:
                raise ValueError(f"Unknown message type: {msg_type}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user_id}", extra={"user_id": user_id})
    except Exception as e:
        logger.error("WebSocket error", extra={"user_id": user_id, "error": str(e)})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        await manager.disconnect(user_id)


# Import circular dependencies at the end to avoid issues.
from app.core.errors.base import AuthorizationError, ValidationError, WebSocketError
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager
from app.models.entities.user import User
from app.core.references import UserRole
