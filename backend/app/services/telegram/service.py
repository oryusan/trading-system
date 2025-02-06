"""
Enhanced Telegram service with comprehensive error handling and monitoring.

Features:
- Message queue management 
- Rate limiting with backoff
- Error notification system
- Rich logging context
- WebSocket status monitoring
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationBuilder
import asyncio
from decimal import Decimal

from app.core.errors import ValidationError, ConfigurationError, ServiceError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.config.settings import settings

logger = get_logger(__name__)

class TelegramService:
    """
    Enhanced Telegram service for trading system notifications.
    
    Features:
    - Message queue management with rate limiting
    - Rich error context and logging
    - WebSocket integration for connection status
    - Performance metric tracking
    """
    
    def __init__(self):
        """Initialize Telegram service with configuration validation."""
        try:
            # Validate configuration
            if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
                raise ConfigurationError(
                    "Missing Telegram configuration",
                    context={
                        "has_token": bool(settings.TELEGRAM_BOT_TOKEN),
                        "has_chat_id": bool(settings.TELEGRAM_CHAT_ID)
                    }
                )

            self.token = settings.TELEGRAM_BOT_TOKEN
            self.chat_id = settings.TELEGRAM_CHAT_ID
            self.app: Optional[Application] = None
            self.bot: Optional[Bot] = None
            
            # Message queue configuration
            self._message_queue: asyncio.Queue = asyncio.Queue(
                maxsize=settings.TELEGRAM_MESSAGE_QUEUE_SIZE
            )
            self._message_task: Optional[asyncio.Task] = None
            self._connected = False
            
            # Error tracking
            self._error_counts: Dict[str, int] = {}
            self._last_notification: Dict[str, datetime] = {}
            self._notification_lock = asyncio.Lock()
            
            self.logger = logger.getChild('telegram_service')

        except Exception as e:
            logger.error(
                "Failed to initialize Telegram service",
                extra={"error": str(e)}
            )
            raise

    async def start(self) -> None:
        """Start Telegram service and message processor."""
        try:
            if self._connected:
                raise ServiceError(
                    "Telegram service already running",
                    context={"app_status": str(self.app)}
                )

            self.app = await ApplicationBuilder().token(self.token).build()
            self.bot = self.app.bot
            
            # Start message processor
            self._message_task = asyncio.create_task(self._process_message_queue())
            self._connected = True
            
            self.logger.info("Telegram service started successfully")

            # Send startup notification
            await self.send_message(
                "🟢 Trading Bot System Online\n"
                f"Version: {settings.VERSION}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

        except Exception as e:
            self._connected = False
            await handle_api_error(
                error=e,
                context={"service": "telegram"},
                log_message="Failed to start Telegram service"
            )
            raise ServiceError(
                "Failed to start Telegram service",
                context={"error": str(e)}
            )

    async def stop(self) -> None:
        """Stop service and cleanup resources."""
        try:
            if self._connected:
                await self.send_message(
                    "🔴 Trading Bot System Shutting Down\n"
                    f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
            
            # Cancel message processor
            if self._message_task:
                self._message_task.cancel()
                try:
                    await self._message_task
                except asyncio.CancelledError:
                    pass
            
            # Shutdown application
            if self.app:
                await self.app.shutdown()
                
            self._connected = False
            self.logger.info("Telegram service stopped")
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": "telegram"},
                log_message="Error during Telegram service shutdown"
            )

    async def _process_message_queue(self) -> None:
        """Process messages from queue with rate limiting."""
        while True:
            try:
                message, parse_mode = await self._message_queue.get()
                
                if not self._connected or not self.bot:
                    self.logger.warning("Cannot send message - service not connected")
                    continue
                    
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
                
                # Rate limiting - 30 messages per second
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
                await asyncio.sleep(1)  # Error cooldown

    async def send_message(
        self,
        message: str,
        parse_mode: str = "HTML",
        retry_count: int = 3
    ) -> bool:
        """Send message with retry logic."""
        try:
            if not message.strip():
                raise ValidationError(
                    "Empty message",
                    context={"parse_mode": parse_mode}
                )

            for attempt in range(retry_count):
                try:
                    await self._message_queue.put((message, parse_mode))
                    return True
                except asyncio.QueueFull:
                    if attempt == retry_count - 1:
                        raise ServiceError(
                            "Message queue full",
                            context={
                                "queue_size": self._message_queue.qsize(),
                                "max_size": settings.TELEGRAM_MESSAGE_QUEUE_SIZE
                            }
                        )
                    await asyncio.sleep(settings.TELEGRAM_RETRY_DELAY)

            return False

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "message_length": len(message),
                    "parse_mode": parse_mode
                },
                log_message="Failed to queue Telegram message"
            )
            return False

    async def notify_trade_executed(self, trade: Dict) -> None:
        """Send trade execution notification."""
        try:
            required_fields = ["symbol", "side", "size", "entry_price", "leverage"]
            missing_fields = [f for f in required_fields if f not in trade]
            
            if missing_fields:
                raise ValidationError(
                    "Missing required trade fields",
                    context={
                        "missing_fields": missing_fields,
                        "trade_id": trade.get("id")
                    }
                )

            message = (
                f"🔔 <b>Trade Executed</b>\n"
                f"Symbol: {trade['symbol']}\n"
                f"Side: {trade['side']}\n"
                f"Size: {trade['size']}\n"
                f"Entry: {trade['entry_price']}\n"
                f"Leverage: {trade['leverage']}x\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            
            await self.send_message(message)
            
            self.logger.info(
                "Trade execution notification sent",
                extra={
                    "symbol": trade['symbol'],
                    "side": trade['side'],
                    "trade_id": trade.get('id')
                }
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={"trade": trade},
                log_message="Failed to send trade execution notification"
            )

    async def notify_trade_closed(self, trade: Dict) -> None:
        """Send trade closure notification."""
        try:
            if "symbol" not in trade or "pnl" not in trade:
                raise ValidationError(
                    "Missing required trade closure fields",
                    context={
                        "trade_id": trade.get("id"),
                        "fields": list(trade.keys())
                    }
                )

            message = (
                f"💰 <b>Trade Closed</b>\n"
                f"Symbol: {trade['symbol']}\n"
                f"PnL: {trade['pnl']:.2f} USD\n"
                f"ROI: {trade.get('pnl_percentage', 0):.2f}%\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            
            await self.send_message(message)
            
            self.logger.info(
                "Trade closure notification sent",
                extra={
                    "symbol": trade['symbol'],
                    "pnl": trade['pnl'],
                    "trade_id": trade.get('id')
                }
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={"trade": trade},
                log_message="Failed to send trade closure notification" 
            )

    async def notify_bot_status(self, bot_id: str, status: str) -> None:
        """Send bot status update notification."""
        try:
            # Get bot via reference manager
            bot = await reference_manager.get_reference(bot_id)
            if not bot:
                raise ValidationError(
                    "Bot not found",
                    context={"bot_id": bot_id}
                )

            # Get WebSocket status for bot's accounts
            accounts = await reference_manager.get_references(
                source_type="Bot",
                reference_id=bot_id
            )
            
            ws_status = []
            for account in accounts:
                ws_client = await ws_manager.get_connection(str(account["id"]))
                if ws_client:
                    is_healthy = await ws_client.is_healthy()
                    ws_status.append(
                        f"{account['exchange'].upper()}: "
                        f"{'✅' if is_healthy else '❌'}"
                    )

            message = (
                f"🤖 <b>Bot Status Update</b>\n"
                f"Bot: {bot['name']}\n"
                f"Status: {status}\n"
                f"Connections: {', '.join(ws_status)}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            
            await self.send_message(message)
            
            self.logger.info(
                "Bot status notification sent",
                extra={
                    "bot_id": bot_id,
                    "status": status
                }
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "bot_id": bot_id,
                    "status": status
                },
                log_message="Failed to send bot status notification"
            )

    async def notify_error(
        self,
        error_type: str,
        details: str,
        severity: str = "ERROR"
    ) -> None:
        """Send error notification with rate limiting."""
        try:
            async with self._notification_lock:
                # Check notification cooldown
                now = datetime.utcnow()
                last_time = self._last_notification.get(error_type)
                
                if last_time and (now - last_time).total_seconds() < settings.ERROR_NOTIFICATION_COOLDOWN:
                    return
                
                # Update error tracking
                self._error_counts[error_type] = self._error_counts.get(error_type, 0) + 1
                self._last_notification[error_type] = now

            severity_emoji = {
                "ERROR": "⚠️",
                "WARNING": "⚡",
                "CRITICAL": "🔥"
            }.get(severity.upper(), "⚠️")

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
                extra={
                    "error_type": error_type,
                    "severity": severity,
                    "count": self._error_counts[error_type]
                }
            )

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "error_type": error_type,
                    "severity": severity,
                    "details": details
                },
                log_message="Failed to send error notification"
            )

    async def send_daily_summary(self, accounts: List[str]) -> None:
        """Send daily performance summary."""
        try:
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
                        self.logger.warning(
                            f"Account {account_id} not found for daily summary"
                        )
                        continue

                    metrics = await performance_service.get_account_metrics(
                        account_id=account_id,
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
                        context={"account_id": account_id},
                        log_message="Error processing account summary"
                    )
                    continue

            message = (
                f"📊 <b>Daily Summary</b>\n"
                f"Total PnL: {float(total_pnl):.2f} USD\n"
                f"Total Trades: {total_trades}\n\n"
                f"{''.join(summary_lines)}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
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

        except Exception as e:
            await handle_api_error(
                error=e,
                context={"accounts": accounts},
                log_message="Failed to send daily summary"
            )


# Import at end to avoid circular dependencies
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.telegram.handlers import register_handlers

# Create singleton instance
telegram_bot = TelegramService()