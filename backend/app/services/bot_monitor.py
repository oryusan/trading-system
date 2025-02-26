"""
Bot monitoring service with WebSocket integration and error handling.

Features:
- Bot lifecycle management
- WebSocket integration 
- Reference validation
- Error recovery
- Performance monitoring
"""

from typing import Dict, Any, Optional
import asyncio
from datetime import datetime

from app.core.errors.base import ServiceError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class BotMonitorError(ServiceError):
    """Base exception for bot monitoring errors."""
    pass


class BotMonitor:
    """
    Monitors active bots with WebSocket integration and error handling.
    
    Features:
    - Bot lifecycle management
    - WebSocket integration
    - Reference validation 
    - Error recovery
    - Performance tracking
    """

    def __init__(self) -> None:
        """Initialize bot monitor with dependencies."""
        self.active_bots: Dict[str, Dict[str, Any]] = {}
        self.monitor_task: Optional[asyncio.Task] = None
        self.is_running: bool = False
        self.logger = logger  # Using the module-level logger
        self._lock = asyncio.Lock()

    @property
    def now(self) -> datetime:
        """Return the current UTC datetime."""
        return datetime.utcnow()

    async def start_monitoring(self) -> None:
        """Start bot monitoring with error handling."""
        if self.is_running:
            error_context = {"monitor_task": str(self.monitor_task)}
            raise BotMonitorError("Bot monitoring already running", context=error_context)

        try:
            self.is_running = True
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            self.logger.info("Bot monitoring started", extra={"timestamp": self.now.isoformat()})
            # Notify via Telegram (importing locally to avoid circular dependencies)
            from app.services.telegram.service import telegram_bot
            await telegram_bot.send_message(
                f"🟢 Bot Monitor Started\nTime: {self.now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        except Exception as e:
            self.is_running = False
            await handle_api_error(
                error=e,
                context={"service": "bot_monitor"},
                log_message="Failed to start bot monitoring"
            )
            raise BotMonitorError(
                "Failed to start bot monitoring",
                context={"error": str(e), "timestamp": self.now.isoformat()}
            )

    async def stop_monitoring(self) -> None:
        """Stop monitoring and cleanup resources."""
        try:
            self.is_running = False
            if self.monitor_task:
                self.monitor_task.cancel()
                try:
                    await asyncio.gather(self.monitor_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass

            # Close all WebSocket connections concurrently.
            async with self._lock:
                close_tasks = []
                for bot_id, bot_info in self.active_bots.items():
                    for account_id in bot_info.get('accounts', {}):
                        close_tasks.append(self._close_connection_with_logging(bot_id, account_id))
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)

            async with self._lock:
                self.active_bots.clear()

            from app.services.telegram.service import telegram_bot
            await telegram_bot.send_message(
                f"🔴 Bot Monitor Stopped\nTime: {self.now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            self.logger.info("Bot monitoring stopped")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "bot_monitor"},
                log_message="Error during monitor shutdown"
            )

    async def _monitor_loop(self) -> None:
        """Main monitoring loop with error handling."""
        while self.is_running:
            try:
                # Retrieve active bots using reference_manager (imported locally to avoid circular imports)
                from app.services.reference.manager import reference_manager
                active_bots = await reference_manager.get_references(
                    source_type="Bot",
                    filter_params={"status": "active"}
                )
                current_bot_ids = {str(bot.id) for bot in active_bots}
                async with self._lock:
                    tracked_bot_ids = set(self.active_bots.keys())

                # Setup monitoring for new bots.
                for bot in active_bots:
                    bot_id = str(bot.id)
                    if bot_id not in tracked_bot_ids:
                        try:
                            await self._setup_bot_monitoring(bot)
                        except Exception as e:
                            await handle_api_error(
                                error=e,
                                context={"bot_id": bot_id, "action": "setup_monitoring"},
                                log_message="Failed to setup bot monitoring"
                            )
                # Cleanup bots no longer active.
                for bot_id in tracked_bot_ids - current_bot_ids:
                    try:
                        await self._cleanup_bot_monitoring(bot_id)
                    except Exception as e:
                        await handle_api_error(
                            error=e,
                            context={"bot_id": bot_id, "action": "cleanup_monitoring"},
                            log_message="Failed to cleanup bot monitoring"
                        )

                # Update positions and check WebSocket health concurrently.
                await asyncio.gather(
                    self._update_positions(),
                    self._check_websocket_health()
                )

                await asyncio.sleep(3)  # Monitor interval (3 seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={"loop": "monitor"},
                    log_message="Error in monitor loop"
                )
                await asyncio.sleep(3)

    async def _setup_bot_monitoring(self, bot: Any) -> None:
        """Setup monitoring for a new bot."""
        bot_id = str(bot.id)
        try:
            # Validate bot reference.
            from app.services.reference.manager import reference_manager
            is_valid = await reference_manager.validate_reference(
                source_type="BotMonitor",
                target_type="Bot",
                reference_id=bot_id
            )
            if not is_valid:
                raise ValidationError("Invalid bot reference", context={"bot_id": bot_id})

            # Get connected accounts.
            accounts = await reference_manager.get_references(
                source_type="Bot",
                reference_id=bot_id
            )
            bot_data = {
                'bot': bot,
                'accounts': {},
                'last_update': self.now
            }
            async with self._lock:
                self.active_bots[bot_id] = bot_data

            # Setup monitoring for each account.
            for account in accounts:
                await self._setup_account_monitoring(account, bot)
            self.logger.info(
                f"Started monitoring bot {bot.name}",
                extra={"connected_accounts": len(accounts), "timestamp": self.now.isoformat()}
            )
        except ValidationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"bot_id": bot_id, "bot_name": getattr(bot, 'name', 'unknown')},
                log_message="Failed to setup bot monitoring"
            )
            raise BotMonitorError(
                "Failed to setup bot monitoring",
                context={"bot_id": bot_id, "bot_name": getattr(bot, 'name', 'unknown'), "error": str(e)}
            )

    async def _setup_account_monitoring(self, account: Dict[str, Any], bot: Any) -> None:
        """Setup monitoring for an account."""
        account_id = str(account["id"])
        bot_id = str(bot.id)
        try:
            from app.services.websocket.manager import ws_manager
            await ws_manager.create_connection(account_id, account, account.get("exchange", ""))
            channels = ["positions", "orders", "balances"]
            await asyncio.gather(*(ws_manager.subscribe(account_id, channel) for channel in channels))
            async with self._lock:
                if bot_id in self.active_bots:
                    self.active_bots[bot_id]['accounts'][account_id] = account
            self.logger.info(
                "Account monitoring setup complete",
                extra={"account_id": account_id, "exchange": account.get("exchange", "")}
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"account_id": account_id, "exchange": account.get("exchange", "")},
                log_message="Failed to setup account monitoring"
            )
            raise BotMonitorError(
                "Failed to setup account monitoring",
                context={"account_id": account_id, "exchange": account.get("exchange", ""), "error": str(e)}
            )

    async def _cleanup_bot_monitoring(self, bot_id: str) -> None:
        """Cleanup monitoring for an inactive bot."""
        try:
            async with self._lock:
                bot_info = self.active_bots.pop(bot_id, None)
            if not bot_info:
                return
            from app.services.websocket.manager import ws_manager
            tasks = [
                self._close_connection_with_logging(bot_id, account_id)
                for account_id in bot_info.get('accounts', {}).keys()
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.logger.info("Bot monitoring cleaned up", extra={"bot_id": bot_id})
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"bot_id": bot_id},
                log_message="Failed to cleanup bot monitoring"
            )
            raise BotMonitorError(
                "Failed to cleanup bot monitoring",
                context={"bot_id": bot_id, "error": str(e)}
            )

    async def _close_connection_with_logging(self, bot_id: str, account_id: str) -> None:
        """Close a WebSocket connection and log any errors."""
        try:
            from app.services.websocket.manager import ws_manager
            await ws_manager.close_connection(account_id)
        except Exception as e:
            self.logger.error(
                f"Error closing WebSocket for account {account_id}",
                extra={"bot_id": bot_id, "account_id": account_id, "error": str(e)}
            )

    async def _update_positions(self) -> None:
        """Update monitored positions with error handling."""
        async def update_account(account_id: str, account: Dict[str, Any]) -> None:
            try:
                from app.services.websocket.manager import ws_manager
                positions = await ws_manager.get_positions(account_id)
                normalized_positions = {}
                for position in positions:
                    try:
                        from app.services.exchange.factory import symbol_validator
                        validation = await symbol_validator.validate_symbol(position['symbol'], account.get("exchange", ""))
                        position['original_symbol'] = validation['original']
                        position['normalized_symbol'] = validation['normalized']
                        normalized_positions[validation['original']] = position
                    except Exception as ve:
                        self.logger.error(
                            f"Symbol validation failed for {position.get('symbol', '')}",
                            extra={"error": str(ve)}
                        )
                        continue
                from app.services.performance.service import performance_service
                await performance_service.update_daily_performance(
                    account_id=account_id,
                    date=self.now,
                    metrics={"positions": len(normalized_positions), "position_value": sum(float(p.get('notional_value', 0)) for p in normalized_positions.values())}
                )
                if positions:
                    self.logger.info(
                        f"Updated positions for account {account_id}",
                        extra={"position_count": len(positions)}
                    )
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={"account_id": account_id, "action": "update_positions"},
                    log_message="Failed to update positions"
                )

        async with self._lock:
            bots_snapshot = dict(self.active_bots)
        for bot_id, bot_info in bots_snapshot.items():
            tasks = [
                update_account(account_id, account)
                for account_id, account in bot_info.get('accounts', {}).items()
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_websocket_health(self) -> None:
        """Check the health of WebSocket connections."""
        try:
            from app.services.websocket.manager import ws_manager
            ws_status = await ws_manager.verify_connections()
            disconnected = ws_status.get('disconnected', [])
            reconnecting = ws_status.get('reconnecting', [])
            if disconnected:
                await asyncio.gather(*(ws_manager.reconnect(account_id) for account_id in disconnected), return_exceptions=True)
            for account_id in reconnecting:
                self.logger.warning(
                    f"WebSocket reconnecting: {account_id}",
                    extra={"reconnect_status": "in_progress"}
                )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"action": "health_check"},
                log_message="WebSocket health check failed"
            )

    async def get_bot_status(self, bot_id: str) -> Dict[str, Any]:
        """Get detailed status for a specific bot."""
        try:
            async with self._lock:
                bot_info = self.active_bots.get(bot_id)
            if not bot_info:
                return {"error": "Bot not being monitored", "bot_id": bot_id}
            account_status = {}
            from app.services.websocket.manager import ws_manager
            for account_id, account in bot_info.get('accounts', {}).items():
                ws_status = await ws_manager.get_connection_status(account_id)
                positions = None
                if ws_status.get('connected'):
                    try:
                        positions = await ws_manager.get_positions(account_id)
                    except Exception as e:
                        self.logger.error(
                            "Failed to get positions",
                            extra={"account_id": account_id, "error": str(e)}
                        )
                account_status[account_id] = {
                    'positions': positions,
                    'websocket_status': ws_status,
                    'exchange': account.get('exchange')
                }
            return {
                'bot_id': bot_id,
                'bot_name': bot_info['bot'].name if hasattr(bot_info['bot'], 'name') else bot_info['bot'].get('name', ''),
                'status': bot_info['bot'].status if hasattr(bot_info['bot'], 'status') else bot_info['bot'].get('status', ''),
                'accounts': account_status,
                'last_update': bot_info['last_update'].isoformat()
            }
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"bot_id": bot_id},
                log_message="Failed to get bot status"
            )
            raise BotMonitorError(
                "Failed to get bot status",
                context={"bot_id": bot_id, "error": str(e)}
            )

    async def get_monitor_status(self) -> Dict[str, Any]:
        """Get detailed monitoring status."""
        try:
            async with self._lock:
                bots_snapshot = dict(self.active_bots)
            status = {
                "active_bots": len(bots_snapshot),
                "is_running": self.is_running,
                "bots": {}
            }
            from app.services.websocket.manager import ws_manager
            for bot_id, bot_info in bots_snapshot.items():
                bot_status = {
                    "name": bot_info['bot'].name if hasattr(bot_info['bot'], 'name') else bot_info['bot'].get('name', ''),
                    "accounts": len(bot_info.get('accounts', {})),
                    "last_update": bot_info['last_update'].isoformat(),
                    "account_status": {}
                }
                for account_id in bot_info.get('accounts', {}):
                    ws_status = await ws_manager.get_connection_status(account_id)
                    bot_status["account_status"][account_id] = ws_status
                status["bots"][bot_id] = bot_status
            return status
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"action": "get_monitor_status"},
                log_message="Failed to get monitor status"
            )
            raise BotMonitorError("Failed to get monitor status", context={"error": str(e)})

def get_ws_manager() -> Any:
    """Return the global WebSocket manager instance."""
    from app.services.websocket.manager import ws_manager as ws_mgr
    return ws_mgr

# Global instance for use in the application.
bot_monitor = BotMonitor()
