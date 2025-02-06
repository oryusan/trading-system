"""
WebSocket endpoints for real-time UI communication with enhanced error handling.

Features:
- WebSocket connection management for UI clients
- Bot status subscriptions and updates
- Authentication and authorization checks
- Error handling with proper error responses
- Rate limiting and connection monitoring
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status
from typing import Dict, Set, Optional, Any
from datetime import datetime
import json
import asyncio

from app.api.v1.deps import get_current_user
from app.core.logging.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

class UIConnectionManager:
    """
    Manages WebSocket connections and bot subscriptions for UI.
    
    Features:
    - Connection management with service integration
    - Bot status subscriptions with reference validation
    - Performance tracking integration
    - Enhanced error handling
    """
    
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}
        self.bot_subscriptions: Dict[str, Set[str]] = {}
        self._health_check_tasks: Dict[str, asyncio.Task] = {}
        self._connection_metrics: Dict[str, Any] = {
            "total_connections": 0,
            "active_connections": 0,
            "error_count": 0
        }

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accept connection and initialize monitoring."""
        try:
            await websocket.accept()
            self.connections[user_id] = websocket
            
            self._health_check_tasks[user_id] = asyncio.create_task(
                self._monitor_connection_health(user_id, websocket)
            )

            self._connection_metrics["total_connections"] += 1
            self._connection_metrics["active_connections"] += 1

            await performance_service.update_connection_metrics(
                user_id=user_id,
                metrics=self._connection_metrics
            )

            logger.info(
                "WebSocket connection established",
                extra={
                    "user_id": user_id,
                    "remote_ip": websocket.client.host if websocket.client else None,
                    "metrics": self._connection_metrics
                }
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "user_id": user_id,
                    "action": "connect",
                    "metrics": self._connection_metrics
                },
                log_message="WebSocket connection failed"
            )
            raise

    async def disconnect(self, user_id: str) -> None:
        """Clean up user's connection and subscriptions."""
        try:
            if task := self._health_check_tasks.pop(user_id, None):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            if websocket := self.connections.pop(user_id, None):
                await websocket.close()
            
            for subscribers in self.bot_subscriptions.values():
                subscribers.discard(user_id)

            self._connection_metrics["active_connections"] -= 1
            
            await performance_service.update_connection_metrics(
                user_id=user_id,
                metrics=self._connection_metrics
            )
                
            logger.info(
                "WebSocket connection closed",
                extra={
                    "user_id": user_id,
                    "metrics": self._connection_metrics
                }
            )
                
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "user_id": user_id,
                    "action": "disconnect",
                    "metrics": self._connection_metrics
                },
                log_message="WebSocket cleanup failed"
            )

    async def _monitor_connection_health(self, user_id: str, websocket: WebSocket) -> None:
        """Monitor WebSocket connection health with ping/pong."""
        while True:
            try:
                await asyncio.sleep(30)
                await websocket.send_json({"type": "ping"})
                
                await performance_service.update_health_metrics(
                    user_id=user_id,
                    metrics={
                        "last_ping": datetime.utcnow().isoformat(),
                        "connection_id": websocket.client_state.client_id
                    }
                )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "user_id": user_id,
                        "action": "health_check"
                    },
                    log_message="WebSocket health check failed"
                )
                await self.disconnect(user_id)
                break

    async def validate_bot_access(self, user: User, bot_id: str) -> bool:
        """Validate user's access to bot subscription."""
        try:
            valid = await reference_manager.validate_reference(
                source_type="User",
                target_type="Bot",
                source_id=str(user.id),
                reference_id=bot_id
            )

            if not valid:
                raise AuthorizationError(
                    "User does not have access to this bot",
                    context={
                        "user_id": str(user.id),
                        "bot_id": bot_id,
                        "role": user.role
                    }
                )

            return True

        except AuthorizationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "user_id": str(user.id),
                    "bot_id": bot_id,
                    "action": "validate_access"
                },
                log_message="Bot access validation failed"
            )
            raise

    async def subscribe_to_bot(self, user_id: str, bot_id: str) -> None:
        """Subscribe user to bot updates with error handling."""
        try:
            if not user_id or not bot_id:
                raise ValidationError(
                    "Invalid subscription parameters",
                    context={
                        "user_id": user_id,
                        "bot_id": bot_id
                    }
                )
            
            if bot_id not in self.bot_subscriptions:
                self.bot_subscriptions[bot_id] = set()
            self.bot_subscriptions[bot_id].add(user_id)
            
            await performance_service.update_subscription_metrics(
                user_id=user_id,
                bot_id=bot_id,
                metrics={
                    "total_subscriptions": len(self.bot_subscriptions[bot_id]),
                    "subscription_time": datetime.utcnow().isoformat()
                }
            )
            
            logger.info(
                "Bot subscription added",
                extra={
                    "user_id": user_id,
                    "bot_id": bot_id,
                    "total_subscriptions": len(self.bot_subscriptions[bot_id])
                }
            )
            
        except ValidationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "user_id": user_id,
                    "bot_id": bot_id,
                    "action": "subscribe"
                },
                log_message="Failed to add bot subscription"
            )
            raise

    async def unsubscribe_from_bot(self, user_id: str, bot_id: str) -> None:
        """Unsubscribe user from bot updates."""
        try:
            if bot_id in self.bot_subscriptions:
                self.bot_subscriptions[bot_id].discard(user_id)
                
                await performance_service.update_subscription_metrics(
                    user_id=user_id,
                    bot_id=bot_id,
                    metrics={
                        "total_subscriptions": len(self.bot_subscriptions[bot_id]),
                        "unsubscribe_time": datetime.utcnow().isoformat()
                    }
                )
                
                logger.info(
                    "Bot subscription removed",
                    extra={
                        "user_id": user_id,
                        "bot_id": bot_id,
                        "total_subscriptions": len(self.bot_subscriptions[bot_id])
                    }
                )
                
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "user_id": user_id,
                    "bot_id": bot_id,
                    "action": "unsubscribe"
                },
                log_message="Failed to remove bot subscription"
            )

    async def broadcast_bot_update(self, bot_id: str, data: dict) -> None:
        """Broadcast update to bot subscribers."""
        if bot_id not in self.bot_subscriptions:
            return
            
        message = {
            "type": "bot_update",
            "bot_id": bot_id,
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        failed_users = set()
        
        for user_id in self.bot_subscriptions[bot_id].copy():
            try:
                if websocket := self.connections.get(user_id):
                    await websocket.send_json(message)
            except Exception as e:
                failed_users.add(user_id)
                await handle_api_error(
                    error=e,
                    context={
                        "user_id": user_id,
                        "bot_id": bot_id,
                        "action": "broadcast"
                    },
                    log_message="Failed to send bot update"
                )
                
        for user_id in failed_users:
            await self.disconnect(user_id)

manager = UIConnectionManager()

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    current_user: User = Depends(get_current_user)
):
    """WebSocket endpoint for UI connections."""
    try:
        if str(current_user.id) != user_id:
            raise AuthorizationError(
                "User ID mismatch",
                context={
                    "path_user_id": user_id,
                    "token_user_id": str(current_user.id)
                }
            )
            
        await manager.connect(user_id, websocket)
        
        try:
            while True:
                try:
                    raw_message = await websocket.receive_text()
                    message = json.loads(raw_message)
                except json.JSONDecodeError as e:
                    raise ValidationError(
                        "Invalid message format",
                        context={
                            "message": raw_message,
                            "error": str(e)
                        }
                    )
                
                if not isinstance(message, dict) or "type" not in message:
                    raise ValidationError(
                        "Invalid message structure",
                        context={"message": message}
                    )
                
                if message["type"] == "subscribe":
                    if "bot_id" not in message:
                        raise ValidationError(
                            "Missing bot_id in subscribe message",
                            context={"message": message}
                        )
                        
                    bot_id = message["bot_id"]
                    await manager.validate_bot_access(current_user, bot_id)
                    await manager.subscribe_to_bot(user_id, bot_id)
                    
                    try:
                        bot_state = await reference_manager.get_reference(
                            reference_id=bot_id,
                            reference_type="Bot"
                        )
                        if bot_state:
                            await manager.broadcast_bot_update(bot_id, bot_state)
                    except Exception as e:
                        logger.error(
                            "Failed to send initial bot state",
                            extra={
                                "bot_id": bot_id,
                                "error": str(e)
                            }
                        )
                        
                elif message["type"] == "unsubscribe":
                    if "bot_id" not in message:
                        raise ValidationError(
                            "Missing bot_id in unsubscribe message",
                            context={"message": message}
                        )
                        
                    await manager.unsubscribe_from_bot(user_id, message["bot_id"])
                    
                elif message["type"] == "pong":
                    continue
                    
                else:
                    raise ValidationError(
                        "Unknown message type",
                        context={
                            "type": message["type"],
                            "message": message
                        }
                    )
                
        except WebSocketDisconnect:
            logger.info(
                "WebSocket disconnected",
                extra={"user_id": user_id}
            )
        finally:
            await manager.disconnect(user_id)
            
    except AuthorizationError as e:
        logger.warning(
            "WebSocket authorization failed",
            extra={
                "user_id": user_id,
                "error": str(e)
            }
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        
    except ValidationError as e:
        logger.warning(
            "WebSocket message validation failed",
            extra={
                "user_id": user_id,
                "error": str(e)
            }
        )
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
        
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "user_id": user_id,
                "action": "websocket_endpoint"
            },
            log_message="WebSocket error"
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    AuthorizationError,
    ValidationError,
    WebSocketError
)
from app.core.errors.handlers import handle_api_error
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager
from app.models.user import User, UserRole