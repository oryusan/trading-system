"""
Enhanced cron job service with improved error handling and service integration.

Features:
- Enhanced error handling and recovery
- Service integration with dependency injection
- Performance monitoring and metrics
- Improved WebSocket health checks
- Comprehensive logging
"""

from typing import Dict, Set, Optional, Any, List
from datetime import datetime, timedelta
import asyncio
from decimal import Decimal
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import (
    ServiceError,
    ValidationError,
    DatabaseError,
    NotFoundError,
    ExchangeError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    ModelState,
    TimeRange,
    PerformanceMetrics,
    BotStatus
)

class CronService:
    """
    Enhanced cron service with comprehensive error handling and monitoring.
    
    Features:
    - Position and balance syncing
    - Performance calculations
    - Data cleanup
    - Symbol verification
    - Telegram notifications
    """

    def __init__(self):
        """Initialize cron service with dependencies."""
        self.scheduler = AsyncIOScheduler()
        self.logger = logger.getChild('cron')
        self._active_jobs = {}
        self._metrics = {
            "sync_failures": 0,
            "cleanup_failures": 0,
            "performance_failures": 0,
            "verification_failures": 0
        }

    async def start(self) -> None:
        """Start scheduled jobs with enhanced error handling."""
        try:
            # Position and balance syncing
            self.scheduler.add_job(
                self.sync_positions,
                CronTrigger.from_crontab(settings.BALANCE_SYNC_CRON),
                id='sync_positions',
                name='Sync Positions and Balances',
                max_instances=1,
                coalesce=True
            )

            # Daily performance calculation
            self.scheduler.add_job(
                self.calculate_daily_performance,
                CronTrigger.from_crontab(settings.DAILY_PERFORMANCE_CRON),
                id='daily_performance',
                name='Calculate Daily Performance',
                max_instances=1,
                coalesce=True
            )

            # Data cleanup
            self.scheduler.add_job(
                self.cleanup_old_data,
                CronTrigger.from_crontab(settings.CLEANUP_CRON),
                id='cleanup',
                name='Clean Old Data',
                max_instances=1,
                coalesce=True
            )

            # Symbol verification
            self.scheduler.add_job(
                self.verify_symbols,
                CronTrigger.from_crontab(settings.SYMBOL_VERIFICATION_CRON),
                id='symbol_verification',
                name='Verify Symbol Mappings',
                max_instances=1,
                coalesce=True
            )

            # Daily summary
            self.scheduler.add_job(
                self.send_daily_summary,
                CronTrigger.from_crontab(settings.DAILY_PERFORMANCE_CRON),
                id='daily_summary',
                name='Send Daily Summary',
                max_instances=1,
                coalesce=True
            )

            self.scheduler.start()
            
            await telegram_bot.send_message(
                "🕒 Cron Service Started\n"
                f"Version: {settings.VERSION}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            
            self.logger.info("Cron service started")

        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "cron"},
                log_message="Failed to start cron service"
            )
            raise ServiceError(
                "Failed to start cron service",
                context={
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def stop(self) -> None:
        """Stop the scheduler with graceful cleanup."""
        try:
            self.scheduler.shutdown()
            await telegram_bot.send_message(
                "🛑 Cron Service Stopped\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            self.logger.info("Cron service stopped")
        except Exception as e:
            self.logger.error(
                "Error stopping cron service",
                extra={"error": str(e)}
            )

    async def sync_positions(self) -> None:
        """
        Sync positions and balances with enhanced error handling.
        
        Features:
        - Reference validation
        - Performance tracking
        - Enhanced error context
        - WebSocket health checks
        """
        sync_logger = self.logger.getChild('sync_positions')
        
        try:
            # Get active accounts using reference manager
            accounts = await reference_manager.get_references(
                source_type="CronJob",
                filter_params={"is_active": True}
            )

            if not accounts:
                sync_logger.info("No active accounts to sync")
                return

            sync_results = {
                "total_accounts": len(accounts),
                "synced_accounts": 0,
                "failed_accounts": 0,
                "total_positions": 0,
                "errors": []
            }

            for account in accounts:
                try:
                    # Get exchange operations
                    operations = await exchange_factory.get_instance(
                        str(account.id),
                        reference_manager
                    )

                    # Check WebSocket health
                    ws_status = await ws_manager.verify_connection(str(account.id))
                    if not ws_status:
                        await ws_manager.reconnect(str(account.id))

                    # Sync positions
                    positions = await operations.get_all_positions()
                    
                    # Update performance metrics
                    balance_info = await operations.get_balance()
                    await performance_service.update_daily_performance(
                        account_id=str(account.id),
                        date=datetime.utcnow(),
                        balance=Decimal(balance_info['balance']),
                        equity=Decimal(balance_info['equity']),
                        metrics={
                            "positions": len(positions),
                            "position_value": sum(
                                Decimal(p.get('notional_value', '0'))
                                for p in positions
                            )
                        }
                    )

                    sync_results["synced_accounts"] += 1
                    sync_results["total_positions"] += len(positions)

                except Exception as e:
                    sync_results["failed_accounts"] += 1
                    sync_results["errors"].append({
                        "account_id": str(account.id),
                        "error": str(e)
                    })
                    await handle_api_error(
                        error=e,
                        context={
                            "account_id": str(account.id),
                            "action": "sync_positions"
                        },
                        log_message="Failed to sync account"
                    )

            if sync_results["failed_accounts"] > 0:
                await telegram_bot.notify_error(
                    "Position Sync Issues",
                    f"Failed to sync {sync_results['failed_accounts']} accounts",
                    severity="WARNING"
                )

            sync_logger.info(
                "Position sync completed",
                extra=sync_results
            )

        except Exception as e:
            self._metrics["sync_failures"] += 1
            await handle_api_error(
                error=e,
                context={"service": "sync_positions"},
                log_message="Position sync failed"
            )
            raise ServiceError(
                "Position sync failed",
                context={
                    "sync_failures": self._metrics["sync_failures"],
                    "error": str(e)
                }
            )

    async def calculate_daily_performance(self) -> None:
        """
        Calculate daily performance with enhanced error handling.
        
        Features:
        - Service integration
        - Enhanced metrics
        - Error recovery
        - Performance tracking
        """
        perf_logger = self.logger.getChild('daily_performance')
        today = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            # Get active accounts
            accounts = await reference_manager.get_references(
                source_type="CronJob",
                filter_params={"is_active": True}
            )

            if not accounts:
                perf_logger.info("No active accounts for performance calculation")
                return

            perf_results = {
                "total_accounts": len(accounts),
                "processed_accounts": 0,
                "failed_accounts": 0,
                "total_trades": 0,
                "total_pnl": Decimal('0'),
                "errors": []
            }

            for account in accounts:
                try:
                    # Get performance data
                    metrics = await performance_service.get_account_metrics(
                        account_id=str(account.id),
                        time_range=TimeRange(
                            start_date=today,
                            end_date=today
                        )
                    )

                    perf_results["processed_accounts"] += 1
                    perf_results["total_trades"] += metrics.total_trades
                    perf_results["total_pnl"] += metrics.total_pnl

                except Exception as e:
                    perf_results["failed_accounts"] += 1
                    perf_results["errors"].append({
                        "account_id": str(account.id),
                        "error": str(e)
                    })
                    await handle_api_error(
                        error=e,
                        context={
                            "account_id": str(account.id),
                            "date": today,
                            "action": "calculate_performance"
                        },
                        log_message="Failed to calculate account performance"
                    )

            if perf_results["failed_accounts"] > 0:
                await telegram_bot.notify_error(
                    "Performance Calculation Issues",
                    f"Failed to process {perf_results['failed_accounts']} accounts",
                    severity="WARNING"
                )

            perf_logger.info(
                "Performance calculation completed",
                extra=perf_results
            )

        except Exception as e:
            self._metrics["performance_failures"] += 1
            await handle_api_error(
                error=e,
                context={
                    "date": today,
                    "service": "daily_performance"
                },
                log_message="Performance calculation failed"
            )
            raise ServiceError(
                "Performance calculation failed",
                context={
                    "performance_failures": self._metrics["performance_failures"],
                    "date": today,
                    "error": str(e)
                }
            )

    async def cleanup_old_data(self) -> None:
        """
        Clean up old data with enhanced validation.
        
        Features:
        - Data integrity checks
        - Service integration
        - Enhanced error handling
        - Performance tracking
        """
        cleanup_logger = self.logger.getChild('cleanup')
        cutoff_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")

        try:
            cleanup_results = {
                "performance_records_deleted": 0,
                "positions_deleted": 0,
                "symbols_deleted": 0,
                "integrity_issues": [],
                "errors": []
            }

            # Clean performance records
            try:
                delete_result = await performance_service.cleanup_old_records(
                    cutoff_date=cutoff_date
                )
                cleanup_results["performance_records_deleted"] = delete_result["deleted_count"]
            except Exception as e:
                cleanup_results["errors"].append({
                    "operation": "performance_cleanup",
                    "error": str(e)
                })

            # Clean position history
            try:
                cutoff_time = datetime.utcnow() - timedelta(days=90)
                position_delete = await reference_manager.cleanup_references(
                    source_type="Position",
                    cutoff_time=cutoff_time
                )
                cleanup_results["positions_deleted"] = position_delete["deleted_count"]
            except Exception as e:
                cleanup_results["errors"].append({
                    "operation": "position_cleanup",
                    "error": str(e)
                })

            # Verify data integrity
            accounts = await reference_manager.get_references(
                source_type="CronJob",
                filter_params={"is_active": True}
            )

            for account in accounts:
                try:
                    metrics = await performance_service.get_account_metrics(
                        account_id=str(account.id),
                        start_date=datetime.utcnow() - timedelta(days=1),
                        end_date=datetime.utcnow()
                    )

                    operations = await exchange_factory.get_instance(
                        str(account.id),
                        reference_manager
                    )
                    balance = await operations.get_balance()

                    if abs(metrics.end_balance - Decimal(str(balance['balance']))) > Decimal('0.01'):
                        cleanup_results["integrity_issues"].append({
                            "account_id": str(account.id),
                            "performance_balance": str(metrics.end_balance),
                            "actual_balance": str(balance['balance'])
                        })
                except Exception as e:
                    cleanup_results["errors"].append({
                        "operation": "integrity_check",
                        "account_id": str(account.id),
                        "error": str(e)
                    })

            # Clean expired symbols
            try:
                symbol_delete = await symbol_validator.cleanup_expired()
                cleanup_results["symbols_deleted"] = symbol_delete["deleted_count"]
            except Exception as e:
                cleanup_results["errors"].append({
                    "operation": "symbol_cleanup",
                    "error": str(e)
                })

            if cleanup_results["integrity_issues"]:
                await telegram_bot.notify_error(
                    "Data Integrity Issues",
                    f"Found {len(cleanup_results['integrity_issues'])} integrity issues",
                    severity="WARNING"
                )

            if cleanup_results["errors"]:
                await telegram_bot.notify_error(
                    "Cleanup Operation Issues",
                    f"Encountered {len(cleanup_results['errors'])} errors during cleanup",
                    severity="WARNING"
                )

            cleanup_logger.info(
                "Data cleanup completed",
                extra=cleanup_results
            )

        except Exception as e:
            self._metrics["cleanup_failures"] += 1
            await handle_api_error(
                error=e,
                context={
                    "cutoff_date": cutoff_date,
                    "service": "cleanup"
                },
                log_message="Data cleanup failed"
            )
            raise ServiceError(
                "Data cleanup failed",
                context={
                    "cleanup_failures": self._metrics["cleanup_failures"],
                    "cutoff_date": cutoff_date,
                    "error": str(e)
                }
            )

    async def verify_symbols(self) -> None:
        """
        Verify symbol mappings with enhanced validation.
        
        Features:
        - Enhanced error handling
        - Symbol validation
        - Performance tracking
        - Service integration
        """
        verify_logger = self.logger.getChild('symbol_verify')
        
        try:
            # Get active bots using reference manager
            active_bots = await reference_manager.get_references(
                source_type="CronJob",
                filter_params={"status": BotStatus.ACTIVE}
            )

            if not active_bots:
                verify_logger.info("No active bots for symbol verification")
                return

            verification_results = {
                "total_bots": len(active_bots),
                "verified_symbols": 0,
                "failed_symbols": 0,
                "processed_accounts": 0,
                "errors": []
            }

            for bot in active_bots:
                try:
                    # Get connected accounts
                    accounts = await reference_manager.get_references(
                        source_type="Bot",
                        reference_id=str(bot["id"])
                    )
                    
                    for account in accounts:
                        try:
                            # Get recent positions from exchange
                            operations = await exchange_factory.get_instance(
                                str(account["id"]),
                                reference_manager
                            )
                            
                            positions = await operations.get_all_positions()
                            used_symbols = {pos["symbol"] for pos in positions}
                            
                            # Verify each symbol
                            for symbol in used_symbols:
                                try:
                                    await symbol_validator.validate_symbol(
                                        symbol=symbol,
                                        exchange_type=account["exchange"],
                                        force_validation=True
                                    )
                                    verification_results["verified_symbols"] += 1
                                except ValidationError as e:
                                    verification_results["failed_symbols"] += 1
                                    verification_results["errors"].append({
                                        "symbol": symbol,
                                        "exchange": account["exchange"],
                                        "error": str(e)
                                    })
                                    verify_logger.warning(
                                        "Symbol validation failed",
                                        extra={
                                            "symbol": symbol,
                                            "exchange": account["exchange"],
                                            "error": str(e)
                                        }
                                    )
                            
                            verification_results["processed_accounts"] += 1

                        except Exception as e:
                            verification_results["errors"].append({
                                "account_id": str(account["id"]),
                                "exchange": account["exchange"],
                                "error": str(e)
                            })
                            await handle_api_error(
                                error=e,
                                context={
                                    "account_id": str(account["id"]),
                                    "exchange": account["exchange"]
                                },
                                log_message="Failed to verify account symbols"
                            )

                except Exception as e:
                    await handle_api_error(
                        error=e,
                        context={
                            "bot_id": str(bot["id"]),
                            "action": "account_verification"
                        },
                        log_message="Failed to process bot verification"
                    )

            # Report verification results
            verify_logger.info(
                "Symbol verification completed",
                extra=verification_results
            )

            if verification_results["failed_symbols"] > 0:
                await telegram_bot.notify_error(
                    "Symbol Verification Issues",
                    f"Failed to verify {verification_results['failed_symbols']} symbols",
                    severity="WARNING"
                )

            # Update metrics
            if verification_results["failed_symbols"] > 0:
                self._metrics["verification_failures"] += 1

        except Exception as e:
            self._metrics["verification_failures"] += 1
            await handle_api_error(
                error=e,
                context={"service": "verify_symbols"},
                log_message="Symbol verification failed"
            )
            raise ServiceError(
                "Symbol verification failed",
                context={
                    "verification_failures": self._metrics["verification_failures"],
                    "error": str(e)
                }
            )

    async def send_daily_summary(self) -> None:
        """
        Send daily performance summary with enhanced metrics.
        
        Features:
        - Enhanced performance metrics
        - Service integration
        - Error handling
        - Rich formatting
        """
        summary_logger = self.logger.getChild('daily_summary')
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        try:
            # Get active bots using reference manager
            active_bots = await reference_manager.get_references(
                source_type="CronJob",
                filter_params={"status": BotStatus.ACTIVE}
            )

            if not active_bots:
                summary_logger.info("No active bots found for daily summary")
                return

            summary_results = {
                "total_bots": len(active_bots),
                "processed_bots": 0,
                "failed_bots": 0,
                "total_pnl": Decimal('0'),
                "total_trades": 0,
                "processed_accounts": 0,
                "errors": []
            }

            bot_summaries = []

            for bot in active_bots:
                try:
                    # Get connected accounts
                    accounts = await reference_manager.get_references(
                        source_type="Bot",
                        reference_id=str(bot.id)
                    )

                    if not accounts:
                        continue

                    # Get daily performance for all accounts
                    bot_metrics = {
                        "total_pnl": Decimal('0'),
                        "total_fees": Decimal('0'),
                        "total_trades": 0,
                        "winning_trades": 0,
                        "total_volume": Decimal('0')
                    }

                    for account in accounts:
                        await self._process_account_metrics(
                            account=account,
                            bot=bot,
                            today=today,
                            bot_metrics=bot_metrics,
                            summary_results=summary_results
                        )

                    # Calculate win rate
                    win_rate = 0.0
                    if bot_metrics["total_trades"] > 0:
                        win_rate = (bot_metrics["winning_trades"] / bot_metrics["total_trades"]) * 100

                    # Get recent positions
                    recent_positions = await self._fetch_recent_positions(
                        accounts=accounts,
                        summary_logger=summary_logger
                    )

                    # Sort and limit recent positions
                    recent_positions.sort(key=lambda x: x["closed_at"], reverse=True)
                    recent_positions = recent_positions[:5]

                    # Format bot summary
                    bot_summary = (
                        f"\n🤖 {bot.name}\n"
                        f"PnL: {float(bot_metrics['total_pnl']):.2f} USD\n"
                        f"Fees: {float(bot_metrics['total_fees']):.2f} USD\n"
                        f"Net: {float(bot_metrics['total_pnl'] - bot_metrics['total_fees']):.2f} USD\n"
                        f"Trades: {bot_metrics['total_trades']}\n"
                        f"Win Rate: {win_rate:.1f}%\n"
                        f"Volume: {float(bot_metrics['total_volume']):.2f} USD\n"
                    )

                    if recent_positions:
                        bot_summary += "\nRecent Trades:\n"
                        for pos in recent_positions:
                            bot_summary += (
                                f"- {pos['symbol']}: {float(pos['net_pnl']):.2f} USD "
                                f"({float(pos['pnl_ratio']):.1f}%)\n"
                            )

                    bot_summaries.append(bot_summary)
                    summary_results["total_pnl"] += bot_metrics["total_pnl"]
                    summary_results["total_trades"] += bot_metrics["total_trades"]
                    summary_results["processed_bots"] += 1

                except Exception as e:
                    summary_results["failed_bots"] += 1
                    summary_results["errors"].append({
                        "bot_id": str(bot.id),
                        "bot_name": bot.name,
                        "error": str(e)
                    })
                    await handle_api_error(
                        error=e,
                        context={
                            "bot_id": str(bot.id),
                            "bot_name": bot.name
                        },
                        log_message="Failed to process bot summary"
                    )

            # Send overall summary
            if bot_summaries:
                system_summary = (
                    f"📊 Trading System Daily Summary\n"
                    f"Date: {today}\n"
                    f"Total PnL: {float(summary_results['total_pnl']):.2f} USD\n"
                    f"Total Trades: {summary_results['total_trades']}\n"
                    f"\nBot Performance:"
                )
                
                full_message = system_summary + "".join(bot_summaries)
                await telegram_bot.send_message(full_message)
                
                summary_logger.info(
                    "Sent daily summary",
                    extra={
                        "total_pnl": str(summary_results["total_pnl"]),
                        "total_trades": summary_results["total_trades"],
                        "processed_bots": summary_results["processed_bots"]
                    }
                )
            else:
                await telegram_bot.send_message(
                    "📊 No trading activity to report for today"
                )

            if summary_results["errors"]:
                await telegram_bot.notify_error(
                    "Daily Summary Issues",
                    f"Encountered {len(summary_results['errors'])} errors during summary generation",
                    severity="WARNING"
                )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "date": today,
                    "service": "daily_summary"
                },
                log_message="Failed to generate daily summary"
            )
            raise ServiceError(
                "Failed to generate daily summary",
                context={
                    "date": today,
                    "error": str(e)
                }
            )

    async def _process_account_metrics(
        self,
        account: Any,
        bot: Any,
        today: str,
        bot_metrics: Dict[str, Any],
        summary_results: Dict[str, Any]
    ) -> None:
        """Process metrics for a single account."""
        try:
            metrics = await performance_service.get_account_metrics(
                account_id=str(account.id),
                time_range=TimeRange(
                    start_date=today,
                    end_date=today
                )
            )

            bot_metrics["total_pnl"] += metrics.total_pnl
            bot_metrics["total_fees"] += (metrics.trading_fees + metrics.funding_fees)
            bot_metrics["total_trades"] += metrics.total_trades
            bot_metrics["winning_trades"] += metrics.winning_trades
            bot_metrics["total_volume"] += metrics.total_volume

            summary_results["processed_accounts"] += 1

        except Exception as e:
            summary_results["errors"].append({
                "account_id": str(account.id),
                "error": str(e)
            })
            await handle_api_error(
                error=e,
                context={
                    "account_id": str(account.id),
                    "bot_id": str(bot.id),
                    "action": "get_metrics"
                },
                log_message="Failed to get account metrics"
            )

    async def _fetch_recent_positions(
        self,
        accounts: List[Any],
        summary_logger: Any
    ) -> List[Dict[str, Any]]:
        """Fetch recent positions for a list of accounts."""
        recent_positions = []
        for account in accounts:
            try:
                operations = await exchange_factory.get_instance(
                    str(account.id),
                    reference_manager
                )
                positions = await operations.get_position_history(
                    start_time=datetime.utcnow() - timedelta(hours=24),
                    end_time=datetime.utcnow()
                )
                recent_positions.extend(positions)
            except Exception as e:
                summary_logger.warning(
                    f"Failed to get recent positions for account {account.id}",
                    extra={"error": str(e)}
                )
        
        # Sort and limit recent positions
        recent_positions.sort(key=lambda x: x["closed_at"], reverse=True)
        return recent_positions[:5]

    async def _log_error_with_context(
        self,
        error: Exception,
        context: Dict[str, Any],
        log_message: str,
        summary_results: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log error with consistent formatting."""
        if summary_results is not None:
            error_entry = {
                **{k: v for k, v in context.items() if k != "action"},
                "error": str(error)
            }
            summary_results["errors"].append(error_entry)
        
        await handle_api_error(
            error=error,
            context=context,
            log_message=log_message
        )

# Move imports to end to avoid circular dependencies
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config.settings import settings
from app.services.exchange.factory import exchange_factory, symbol_validator
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.telegram.service import telegram_bot

logger = get_logger(__name__)

# Create singleton instance
cron_service = CronService()