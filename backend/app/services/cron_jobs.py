"""
Revised cron_jobs.py

This simplified Cron Service schedules background jobs using APScheduler.
It removes redundant startup/cleanup functions so that scheduling is centralized.
You can call cron_service.start() from your main startup routine to begin scheduling,
and cron_service.stop() during shutdown.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config.settings import settings
from app.core.errors.base import ServiceError, ValidationError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

class CronService:
    """
    Simplified Cron Service

    This service schedules cron tasks (such as syncing positions,
    calculating performance, cleaning up old data, verifying symbols,
    and sending daily summaries) using APScheduler.
    """
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self.logger = get_logger("cron_service")
    
    # ---------------------------
    # Cron Task Functions
    # ---------------------------
    async def sync_positions(self) -> None:
        """Sync positions and balances for active accounts."""
        try:
            from app.services.reference.manager import reference_manager
            accounts = await reference_manager.get_references(source_type="CronJob", filter_params={"is_active": True})
            if not accounts:
                self.logger.info("No active accounts to sync")
                return
            results = []
            for account in accounts:
                try:
                    # Placeholder: call the exchange operations to sync positions.
                    results.append({"account_id": str(account["id"]), "synced": True})
                except Exception as e:
                    results.append({"account_id": str(account["id"]), "synced": False, "error": str(e)})
            self.logger.info("Position sync completed", extra={"results": results})
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "sync_positions"},
                log_message="Position sync failed"
            )
            raise ServiceError("Position sync failed", context={"error": str(e)})
    
    async def calculate_daily_performance(self) -> None:
        """Calculate daily performance metrics."""
        try:
            from app.services.reference.manager import reference_manager
            accounts = await reference_manager.get_references(source_type="CronJob", filter_params={"is_active": True})
            if not accounts:
                self.logger.info("No active accounts for performance calculation")
                return
            for account in accounts:
                # Placeholder: Insert real performance calculation logic here.
                self.logger.info(f"Calculated performance for account {account['id']}")
            self.logger.info("Daily performance calculation completed")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "daily_performance"},
                log_message="Performance calculation failed"
            )
            raise ServiceError("Performance calculation failed", context={"error": str(e)})
    
    async def cleanup_old_data(self) -> None:
        """Clean up old performance and trade records."""
        try:
            cutoff_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
            # Placeholder: call your database cleanup functions here.
            self.logger.info("Old data cleanup completed", extra={"cutoff_date": cutoff_date})
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"cutoff_date": cutoff_date, "service": "cleanup"},
                log_message="Data cleanup failed"
            )
            raise ServiceError("Data cleanup failed", context={"error": str(e)})
    
    async def verify_symbols(self) -> None:
        """Verify symbol mappings for active bots."""
        try:
            from app.services.reference.manager import reference_manager
            active_bots = await reference_manager.get_references(source_type="CronJob", filter_params={"status": "active"})
            if not active_bots:
                self.logger.info("No active bots for symbol verification")
                return
            # Placeholder: Validate symbol information for each bot.
            self.logger.info("Symbol verification completed")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "verify_symbols"},
                log_message="Symbol verification failed"
            )
            raise ServiceError("Symbol verification failed", context={"error": str(e)})
    
    async def send_daily_summary(self) -> None:
        """Send daily performance summary to Telegram."""
        try:
            from app.services.reference.manager import reference_manager
            from app.services.telegram.service import telegram_bot
            accounts = await reference_manager.get_references(source_type="CronJob", filter_params={"is_active": True})
            if not accounts:
                self.logger.info("No active accounts found for daily summary")
                return
            total_pnl = Decimal('0')
            total_trades = 0
            summary_lines = []
            for account in accounts:
                try:
                    from app.services.performance.service import performance_service
                    metrics = await performance_service.get_account_metrics(
                        account_id=str(account["id"]),
                        date=datetime.utcnow()
                    )
                    total_pnl += Decimal(str(metrics["total_pnl"]))
                    total_trades += metrics["total_trades"]
                    summary_lines.append(
                        f"Account: {account['id']}\n"
                        f"PnL: {metrics['total_pnl']:.2f} USD\n"
                        f"Trades: {metrics['total_trades']}\n"
                        f"Win Rate: {metrics['win_rate']:.1f}%\n"
                    )
                except Exception as e:
                    await handle_api_error(
                        error=e,
                        context={"account_id": str(account["id"])},
                        log_message="Error fetching account performance"
                    )
                    summary_lines.append(
                        f"❌ Account: {account['id']}\nError fetching performance\n"
                    )
            message = (
                f"📊 <b>Daily Summary</b>\n\n"
                f"Total PnL: {float(total_pnl):.2f} USD\n"
                f"Total Trades: {total_trades}\n\n"
                f"{''.join(summary_lines)}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            await telegram_bot.send_message(message)
            self.logger.info(
                "Daily summary sent",
                extra={"account_count": len(accounts), "total_pnl": float(total_pnl), "total_trades": total_trades}
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "daily_summary"},
                log_message="Daily summary failed"
            )
            raise ServiceError("Daily summary failed", context={"error": str(e)})
    
    # ---------------------------
    # Simplified Start and Stop
    # ---------------------------
    def start(self) -> None:
        """
        Schedule cron jobs using APScheduler and start the scheduler.
        
        This method is now designed to be called from your main startup routine.
        """
        self.logger.info("Starting Cron Service...")
        self.scheduler.add_job(
            self.sync_positions,
            CronTrigger.from_crontab(settings.cron.BALANCE_SYNC_CRON),
            id='sync_positions',
            name='Sync Positions and Balances',
            max_instances=1,
            coalesce=True
        )
        self.scheduler.add_job(
            self.calculate_daily_performance,
            CronTrigger.from_crontab(settings.cron.DAILY_PERFORMANCE_CRON),
            id='daily_performance',
            name='Calculate Daily Performance',
            max_instances=1,
            coalesce=True
        )
        self.scheduler.add_job(
            self.cleanup_old_data,
            CronTrigger.from_crontab(settings.cron.CLEANUP_CRON),
            id='cleanup',
            name='Cleanup Old Data',
            max_instances=1,
            coalesce=True
        )
        self.scheduler.add_job(
            self.verify_symbols,
            CronTrigger.from_crontab(settings.cron.SYMBOL_VERIFICATION_CRON),
            id='symbol_verification',
            name='Verify Symbols',
            max_instances=1,
            coalesce=True
        )
        self.scheduler.add_job(
            self.send_daily_summary,
            CronTrigger.from_crontab(settings.cron.DAILY_PERFORMANCE_CRON),
            id='daily_summary',
            name='Send Daily Summary',
            max_instances=1,
            coalesce=True
        )
        self.scheduler.start()
        self.logger.info("Cron Service started and jobs scheduled")

    def stop(self) -> None:
        """
        Shut down the APScheduler scheduler.
        """
        self.scheduler.shutdown()
        self.logger.info("Cron Service stopped")

# Global instance for use in the application
cron_service = CronService()
