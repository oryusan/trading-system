"""
Telegram service with lazy initialization and comprehensive error handling.

Key changes:
• Removed the explicit start() method.
• Lazy initialization is now performed within send_message() if the service isn’t already connected.
• The stop() method is simplified to shut down background tasks if needed.
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Optional, Dict, List

from telegram import Bot
from telegram.ext import Application, ApplicationBuilder

from app.core.config.settings import settings
from app.core.errors.base import ConfigurationError, ServiceError, ValidationError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager

logger = get_logger(__name__)


def handle_service_errors(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"function": func.__name__},
                log_message=f"Error in {func.__name__}"
            )
            raise
    return wrapper


class TelegramService:
    def __init__(self) -> None:
        if not settings.telegram.TELEGRAM_BOT_TOKEN or not settings.telegram.TELEGRAM_CHAT_ID:
            raise ConfigurationError(
                "Missing Telegram configuration",
                context={
                    "has_token": bool(settings.telegram.TELEGRAM_BOT_TOKEN),
                    "has_chat_id": bool(settings.telegram.TELEGRAM_CHAT_ID)
                }
            )
        self.token = settings.telegram.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.telegram.TELEGRAM_CHAT_ID
        self.app: Optional[Application] = None
        self.bot: Optional[Bot] = None
        self._connected: bool = False
        self._message_queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.telegram.TELEGRAM_MESSAGE_QUEUE_SIZE
        )
        self._message_task: Optional[asyncio.Task] = None
        self._error_counts: Dict[str, int] = {}
        self._last_notification: Dict[str, datetime] = {}
        self._notification_lock = asyncio.Lock()
        self.logger = get_logger("telegram_service")

    def _current_time_str(self) -> str:
        return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    async def _lazy_init(self) -> None:
        """
        Lazily initialize the Telegram Application and bot.
        This function is called automatically when a message is sent and the service is not yet connected.
        """
        if self._connected:
            return
        # Build the Telegram Application and get the Bot instance
        self.app = await ApplicationBuilder().token(self.token).build()
        self.bot = self.app.bot
        # Start the background message processor
        self._message_task = asyncio.create_task(self._process_message_queue())
        self._connected = True
        self.logger.info("Telegram service lazy-initialized")
        # Optionally, send an initial startup notification directly (bypassing lazy init check)
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"🟢 Trading Bot System Online\nVersion: {settings.app.VERSION}\nTime: {self._current_time_str()}",
                parse_mode="HTML"
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"function": "_lazy_init", "chat_id": self.chat_id},
                log_message="Failed to send startup message"
            )

    @handle_service_errors
    async def stop(self) -> None:
        """
        Stop the Telegram service by canceling background tasks and shutting down the app.
        """
        if self._connected:
            try:
                await self.send_message(
                    f"🔴 Trading Bot System Shutting Down\nTime: {self._current_time_str()}"
                )
            except Exception:
                pass  # Even if sending fails, proceed with shutdown
        if self._message_task:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
        if self.app:
            await self.app.shutdown()
        self._connected = False
        self.logger.info("Telegram service stopped")

    async def _process_message_queue(self) -> None:
        """
        Continuously process messages from the internal queue.
        """
        while True:
            try:
                message, parse_mode = await self._message_queue.get()
                if not self._connected or not self.bot:
                    self.logger.warning("Cannot send message – service not connected")
                    self._message_queue.task_done()
                    continue
                await self.bot.send_message(
                    chat_id=self.chat_id, text=message, parse_mode=parse_mode
                )
                await asyncio.sleep(0.035)
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={"message_type": "telegram"},
                    log_message="Failed to process Telegram message"
                )
                await asyncio.sleep(1)

    @handle_service_errors
    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to the configured Telegram chat.
        If the service is not connected, lazy initialization is triggered.
        """
        if not self._connected:
            await self._lazy_init()
        if not message.strip():
            raise ValidationError("Empty message", context={"parse_mode": parse_mode})
        await self._message_queue.put((message, parse_mode))
        return True

    @handle_service_errors
    async def notify_trade_executed(self, trade: Dict) -> None:
        required_fields = ["symbol", "side", "size", "entry_price", "leverage"]
        missing = [f for f in required_fields if f not in trade]
        if missing:
            raise ValidationError(
                "Missing required trade fields",
                context={"missing_fields": missing, "trade_id": trade.get("id")}
            )
        message = (
            f"🔔 <b>Trade Executed</b>\n"
            f"Symbol: {trade['symbol']}\n"
            f"Side: {trade['side']}\n"
            f"Size: {trade['size']}\n"
            f"Entry: {trade['entry_price']}\n"
            f"Leverage: {trade['leverage']}x\n"
            f"Time: {self._current_time_str()}"
        )
        await self.send_message(message)
        self.logger.info(
            "Trade execution notification sent",
            extra={"symbol": trade['symbol'], "side": trade['side'], "trade_id": trade.get('id')}
        )

    @handle_service_errors
    async def notify_trade_closed(self, trade: Dict) -> None:
        if "symbol" not in trade or "pnl" not in trade:
            raise ValidationError(
                "Missing required trade closure fields",
                context={"trade_id": trade.get("id"), "fields": list(trade.keys())}
            )
        message = (
            f"💰 <b>Trade Closed</b>\n"
            f"Symbol: {trade['symbol']}\n"
            f"PnL: {trade['pnl']:.2f} USD\n"
            f"ROI: {trade.get('pnl_percentage', 0):.2f}%\n"
            f"Time: {self._current_time_str()}"
        )
        await self.send_message(message)
        self.logger.info(
            "Trade closure notification sent",
            extra={"symbol": trade['symbol'], "pnl": trade['pnl'], "trade_id": trade.get('id')}
        )

    @handle_service_errors
    async def notify_bot_status(self, bot_id: str, status: str) -> None:
        bot = await reference_manager.get_reference(bot_id)
        if not bot:
            raise ValidationError("Bot not found", context={"bot_id": bot_id})
        accounts = await reference_manager.get_references(
            source_type="Bot",
            reference_id=bot_id,
        )
        ws_status = []
        for acc in accounts:
            ws_client = await ws_manager.get_connection(str(acc["id"]))
            healthy = await ws_client.is_healthy() if ws_client else False
            ws_status.append(f"{acc['exchange'].upper()}: {'✅' if healthy else '❌'}")
        message = (
            f"🤖 <b>Bot Status Update</b>\n"
            f"Bot: {bot['name']}\n"
            f"Status: {status}\n"
            f"Connections: {', '.join(ws_status)}\n"
            f"Time: {self._current_time_str()}"
        )
        await self.send_message(message)
        self.logger.info(
            "Bot status notification sent",
            extra={"bot_id": bot_id, "status": status}
        )

    @handle_service_errors
    async def notify_error(self, error_type: str, details: str, severity: str = "ERROR") -> None:
        async with self._notification_lock:
            now = datetime.utcnow()
            last_time = self._last_notification.get(error_type)
            if last_time and (now - last_time).total_seconds() < settings.error.ERROR_NOTIFICATION_COOLDOWN:
                return
            self._error_counts[error_type] = self._error_counts.get(error_type, 0) + 1
            self._last_notification[error_type] = now

        severity_emoji = {"ERROR": "⚠️", "WARNING": "⚡", "CRITICAL": "🔥"}.get(severity.upper(), "⚠️")
        message = (
            f"{severity_emoji} <b>{severity} Alert</b>\n"
            f"Type: {error_type}\n"
            f"Details: {details}\n"
            f"Occurrence: #{self._error_counts[error_type]}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        await self.send_message(message)
        self.logger.warning(
            "Error notification sent",
            extra={"error_type": error_type, "severity": severity, "count": self._error_counts[error_type]}
        )

    @handle_service_errors
    async def send_daily_summary(self, accounts: List[str]) -> None:
        if not accounts:
            raise ValidationError(
                "No accounts provided for daily summary",
                context={"timestamp": datetime.utcnow().isoformat()}
            )
        total_pnl = Decimal('0')
        total_trades = 0
        summary_lines = []
        for account_id in accounts:
            try:
                account = await reference_manager.get_reference(account_id)
                if not account:
                    self.logger.warning(f"Account {account_id} not found for daily summary")
                    continue
                metrics = await performance_service.get_account_metrics(
                    account_id=str(account["id"]),
                    date=datetime.utcnow()
                )
                total_pnl += Decimal(str(metrics["total_pnl"]))
                total_trades += metrics["total_trades"]
                summary_lines.append(
                    f"Account: {account_id}\n"
                    f"PnL: {metrics['total_pnl']:.2f} USD\n"
                    f"Trades: {metrics['total_trades']}\n"
                    f"Win Rate: {metrics['win_rate']:.1f}%\n"
                )
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={"account_id": str(account_id)},
                    log_message="Error processing account summary"
                )
                summary_lines.append(
                    f"❌ Account: {account_id}\nError fetching performance\n"
                )
        message = (
            f"📊 <b>Daily Summary</b>\n\n"
            f"Total PnL: {float(total_pnl):.2f} USD\n"
            f"Total Trades: {total_trades}\n\n"
            f"{''.join(summary_lines)}\n"
            f"Time: {self._current_time_str()}"
        )
        await self.send_message(message)
        self.logger.info(
            "Daily summary sent",
            extra={
                "account_count": len(accounts),
                "total_pnl": float(total_pnl),
                "total_trades": total_trades
            }
        )


@handle_service_errors
async def _init_telegram_app():
    """Helper to initialize the Telegram Application and register handlers."""
    from app.services.telegram.handlers import register_handlers
    app = await ApplicationBuilder().token(settings.telegram.TELEGRAM_BOT_TOKEN).build()
    register_handlers(app)
    return app


# Create a global singleton instance
telegram_bot = TelegramService()
