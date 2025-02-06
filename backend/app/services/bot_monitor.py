"""
Enhanced bot monitoring service with WebSocket integration and error handling.

Features:
- Bot lifecycle management
- WebSocket integration 
- Reference validation
- Error recovery
- Performance monitoring
"""

from typing import Dict, List, Optional, Any, Set
import asyncio
from datetime import datetime, timedelta

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

    def __init__(self):
        """Initialize bot monitor with dependencies."""
        self.active_bots: Dict[str, Dict] = {}
        self.monitor_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.logger = logger.getChild('bot_monitor')
        self._lock = asyncio.Lock()

    async def start_monitoring(self) -> None:
        """Start bot monitoring with error handling."""
        try:
            if self.is_running:
                raise BotMonitorError(
                    "Bot monitoring already running",
                    context={"monitor_task": str(self.monitor_task)}
                )

            self.is_running = True
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            
            self.logger.info(
                "Bot monitoring started",
                extra={"timestamp": datetime.utcnow().isoformat()}
            )

            await telegram_bot.send_message(
                "🟢 Bot Monitor Started\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

        except Exception as e:
            self.is_running = False
            await handle_api_error(
                error=e,
                context={"service": "bot_monitor"},
                log_message="Failed to start bot monitoring"
            )
            raise ServiceError(
                "Failed to start bot monitoring",
                context={
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
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

            # Close WebSocket connections
            for bot_id, bot_info in self.active_bots.items():
                for account_id in bot_info['accounts']:
                    try:
                        await ws_manager.close_connection(account_id)
                    except Exception as e:
                        self.logger.error(
                            "Error closing WebSocket",
                            extra={
                                "bot_id": bot_id,
                                "account_id": account_id,
                                "error": str(e)
                            }
                        )
            
            self.active_bots.clear()
            
            await telegram_bot.send_message(
                "🔴 Bot Monitor Stopped\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
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
                # Get active bots using reference manager
                active_bots = await reference_manager.get_references(
                    source_type="Bot",
                    filter_params={"status": BotStatus.ACTIVE}
                )
                
                current_bot_ids = set(str(bot.id) for bot in active_bots)
                tracked_bot_ids = set(self.active_bots.keys())
                
                # Handle new bots
                for bot in active_bots:
                    if str(bot.id) not in self.active_bots:
                        try:
                            await self._setup_bot_monitoring(bot)
                        except Exception as e:
                            await handle_api_error(
                                error=e,
                                context={
                                    "bot_id": str(bot.id),
                                    "action": "setup_monitoring"
                                },
                                log_message="Failed to setup bot monitoring"
                            )
                
                # Handle inactive bots
                for bot_id in tracked_bot_ids - current_bot_ids:
                    try:
                        await self._cleanup_bot_monitoring(bot_id)
                    except Exception as e:
                        await handle_api_error(
                            error=e,
                            context={
                                "bot_id": bot_id,
                                "action": "cleanup_monitoring"
                            },
                            log_message="Failed to cleanup bot monitoring"
                        )
                
                # Update positions
                await self._update_positions()
                
                # Check WebSocket health
                await self._check_websocket_health()
                
                await asyncio.sleep(5)  # Monitor interval
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={"loop": "monitor"},
                    log_message="Error in monitor loop" 
                )
                await asyncio.sleep(5)  # Error cooldown

    async def _setup_bot_monitoring(self, bot: Dict) -> None:
        """Setup monitoring for a new bot."""
        bot_logger = self.logger.getChild(f'bot_{bot["id"]}')
        try:
            # Validate bot references
            if not await reference_manager.validate_reference(
                source_type="BotMonitor",
                target_type="Bot",
                reference_id=str(bot["id"])
            ):
                raise ValidationError(
                    "Invalid bot reference",
                    context={"bot_id": str(bot["id"])}
                )
            
            # Get connected accounts
            accounts = await reference_manager.get_references(
                source_type="Bot",
                reference_id=str(bot["id"])
            )
            
            self.active_bots[str(bot["id"])] = {
                'bot': bot,
                'accounts': {},
                'last_update': datetime.utcnow()
            }
            
            for account in accounts:
                await self._setup_account_monitoring(account, bot)
                
            bot_logger.info(
                f"Started monitoring bot {bot['name']}",
                extra={
                    "connected_accounts": len(accounts),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "bot_id": str(bot["id"]),
                    "bot_name": bot["name"]
                },
                log_message="Failed to setup bot monitoring"
            )
            raise ServiceError(
                "Failed to setup bot monitoring",
                context={
                    "bot_id": str(bot["id"]),
                    "bot_name": bot["name"],
                    "error": str(e)
                }
            )

    async def _setup_account_monitoring(self, account: Dict, bot: Dict) -> None:
        """Setup monitoring for an account."""
        account_logger = self.logger.getChild(f'account_{account["id"]}')
        try:
            # Create WebSocket connection via manager
            await ws_manager.create_connection({
                "identifier": str(account["id"]),
                "exchange_type": account["exchange"],
                "api_key": account["api_key"],
                "api_secret": account["api_secret"],
                "passphrase": account["passphrase"],
                "testnet": account["is_testnet"]
            })
            
            # Subscribe to required channels
            channels = ["positions", "orders", "balances"]
            for channel in channels:
                await ws_manager.subscribe(str(account["id"]), channel)
            
            self.active_bots[str(bot["id"])]['accounts'][str(account["id"])] = account
            
            account_logger.info(
                "Account monitoring setup complete",
                extra={"exchange": account["exchange"]}
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "account_id": str(account["id"]),
                    "exchange": account["exchange"]
                },
                log_message="Failed to setup account monitoring"
            )
            raise ServiceError(
                "Failed to setup account monitoring",
                context={
                    "account_id": str(account["id"]),
                    "exchange": account["exchange"],
                    "error": str(e)
                }
            )

    async def _cleanup_bot_monitoring(self, bot_id: str) -> None:
        """Cleanup monitoring for an inactive bot."""
        bot_logger = self.logger.getChild(f'bot_{bot_id}')
        try:
            bot_info = self.active_bots.pop(bot_id, None)
            if not bot_info:
                return
                
            # Close WebSocket connections for each account
            for account_id in bot_info['accounts'].keys():
                try:
                    await ws_manager.close_connection(account_id)
                except Exception as e:
                    bot_logger.error(
                        f"Error closing WebSocket for account {account_id}",
                        extra={"error": str(e)}
                    )
                    
            bot_logger.info("Bot monitoring cleaned up")
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"bot_id": bot_id},
                log_message="Failed to cleanup bot monitoring"
            )
            raise ServiceError(
                "Failed to cleanup bot monitoring",
                context={
                    "bot_id": bot_id,
                    "error": str(e)
                }
            )

    async def _update_positions(self) -> None:
        """Update monitored positions with error handling."""
        for bot_id, bot_info in self.active_bots.items():
            bot_logger = self.logger.getChild(f'bot_{bot_id}')
            try:
                for account_id, account in bot_info['accounts'].items():
                    try:
                        # Get positions via WebSocket manager
                        positions = await ws_manager.get_positions(account_id)
                        
                        # Validate and normalize positions
                        normalized_positions = {}
                        for position in positions:
                            try:
                                validation = await symbol_validator.validate_symbol(
                                    position['symbol'],
                                    account['exchange']
                                )
                                position['original_symbol'] = validation['original']
                                position['normalized_symbol'] = validation['normalized']
                                normalized_positions[validation['original']] = position
                            except ValidationError as e:
                                bot_logger.error(
                                    f"Symbol validation failed: {position['symbol']}",
                                    extra={"error": str(e)}
                                )
                                continue

                        # Update performance metrics
                        await performance_service.update_daily_performance(
                            account_id=account_id,
                            date=datetime.utcnow(),
                            metrics={
                                "positions": len(normalized_positions),
                                "position_value": sum(
                                    float(p.get('notional_value', 0))
                                    for p in normalized_positions.values()
                                )
                            }
                        )

                        if positions:
                            bot_logger.info(
                                f"Updated positions for account {account_id}",
                                extra={"position_count": len(positions)}
                            )

                    except Exception as e:
                        await handle_api_error(
                            error=e,
                            context={
                                "account_id": account_id,
                                "action": "update_positions"
                            },
                            log_message="Failed to update positions"
                        )

            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "bot_id": bot_id,
                        "action": "update_all_positions"
                    },
                    log_message="Failed to update bot positions"
                )

    async def _check_websocket_health(self) -> None:
        """Check WebSocket connections health."""
        try:
            ws_status = await ws_manager.verify_connections()
            
            for account_id in ws_status.get('disconnected', []):
                try:
                    await ws_manager.reconnect(account_id)
                except Exception as e:
                    await handle_api_error(
                        error=e,
                        context={
                            "account_id": account_id,
                            "action": "reconnect"
                        },
                        log_message="WebSocket reconnection failed"
                    )
                    
            for account_id in ws_status.get('reconnecting', []):
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
            bot_info = self.active_bots.get(bot_id)
            if not bot_info:
                return {
                    "error": "Bot not being monitored",
                    "bot_id": bot_id
                }
                
            account_status = {}
            for account_id, account in bot_info['accounts'].items():
                # Get WebSocket connection status
                ws_status = await ws_manager.get_connection_status(account_id)
                
                # Get positions if connected
                positions = None
                if ws_status.get('connected'):
                    try:
                        positions = await ws_manager.get_positions(account_id)
                    except Exception as e:
                        self.logger.error(
                            "Failed to get positions",
                            extra={
                                "account_id": account_id,
                                "error": str(e)
                            }
                        )
                
                account_status[account_id] = {
                    'positions': positions,
                    'websocket_status': ws_status,
                    'exchange': account['exchange']
                }
                
            return {
                'bot_id': bot_id,
                'bot_name': bot_info['bot']['name'],
                'status': bot_info['bot']['status'],
                'accounts': account_status,
                'last_update': bot_info['last_update'].isoformat()
            }
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"bot_id": bot_id},
                log_message="Failed to get bot status"
            )
            raise ServiceError(
                "Failed to get bot status",
                context={
                    "bot_id": bot_id,
                    "error": str(e)
                }
            )

    async def get_monitor_status(self) -> Dict[str, Any]:
        """Get detailed monitoring status."""
        try:
            status = {
                "active_bots": len(self.active_bots),
                "is_running": self.is_running,
                "bots": {}
            }
            
            for bot_id, bot_info in self.active_bots.items():
                bot_status = {
                    "name": bot_info['bot']['name'],
                    "accounts": len(bot_info['accounts']),
                    "last_update": bot_info['last_update'].isoformat(),
                    "account_status": {}
                }
                
                for account_id in bot_info['accounts']:
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
            raise ServiceError(
                "Failed to get monitor status",
                context={"error": str(e)}
            )


# Move imports to end to avoid circular dependencies
from app.core.errors import ServiceError, ValidationError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.models.bot import BotStatus
from app.services.exchange.factory import symbol_validator
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.telegram.service import telegram_bot

logger = get_logger(__name__)

# Create singleton instance
bot_monitor = BotMonitor()