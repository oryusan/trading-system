"""
Telegram bot command handlers with enhanced error handling and service integration.

Features:
- Command handlers with inline keyboard support
- Enhanced error handling and logging
- Service integration (trading, reference manager)
- WebSocket status monitoring
"""

from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime

from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.config.settings import settings
from app.core.references import WebSocketType
from app.services.exchange.operations import ExchangeOperations

logger = get_logger(__name__)

async def validate_chat(update: Update) -> bool:
    """Validate chat ID matches configured ID."""
    if str(update.effective_chat.id) != settings.TELEGRAM_CHAT_ID:
        await handle_api_error(
            error=ValidationError(
                "Invalid chat ID",
                context={
                    "chat_id": str(update.effective_chat.id),
                    "expected": settings.TELEGRAM_CHAT_ID
                }
            ),
            context={"command": "validate_chat"},
            log_message="Invalid Telegram chat ID"
        )
        return False
    return True

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command with error handling."""
    try:
        if not await validate_chat(update):
            return
            
        message = (
            "🤖 Trading Bot Manager\n\n"
            "Available commands:\n"
            "/status - Get bots status\n"
            "/balance - Get accounts balance\n"
            "/performance - Get today's performance\n"
            "/help - Show this help message"
        )
        
        await update.message.reply_text(message)
        logger.info("Start command processed", extra={"chat_id": update.effective_chat.id})

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"command": "start"},
            log_message="Error processing start command"
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command with enhanced error handling."""
    try:
        if not await validate_chat(update):
            return

        # Get bots via reference manager
        bots = await reference_manager.get_references(
            source_type="TelegramService",
            reference_type="Bot"
        )
        
        if not bots:
            await update.message.reply_text("No bots found")
            return

        # Create keyboard with bot buttons
        keyboard = []
        row = []
        for idx, bot in enumerate(bots, 1):
            row.append(InlineKeyboardButton(
                text=bot["name"],
                callback_data=f"status_{bot['id']}"
            ))
            
            if idx % 3 == 0 or idx == len(bots):
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📊 Select a bot to view status:",
            reply_markup=reply_markup
        )
        
        logger.info(
            "Status command processed",
            extra={
                "chat_id": update.effective_chat.id,
                "bot_count": len(bots)
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"command": "status"},
            log_message="Error processing status command"
        )

async def handle_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot selection callback with error handling."""
    try:
        query = update.callback_query
        await query.answer()
        
        bot_id = query.data.split('_')[1]
        bot = await reference_manager.get_reference(bot_id)
        
        if not bot:
            await query.edit_message_text("Bot not found")
            return

        # Get accounts via reference manager
        accounts = await reference_manager.get_references(
            source_type="Bot",
            reference_id=bot_id
        )

        # Get WebSocket status
        ws_status = {}
        for account in accounts:
            ws_client = await ws_manager.get_connection(str(account["id"]))
            if ws_client:
                ws_status[str(account["id"])] = await ws_client.is_healthy()

        # Format status message
        status_emoji = "🟢" if bot["status"] == "active" else "🔴"
        last_signal = (bot["last_signal"].strftime('%Y-%m-%d %H:%M:%S') 
                      if bot.get("last_signal") else 'N/A')
        
        message = (
            f"{status_emoji} <b>{bot['name']}</b>\n\n"
            f"Status: {bot['status']}\n"
            f"Connected Accounts: {len(accounts)}\n"
            f"Last Signal: {last_signal}\n\n"
        )

        if accounts:
            message += "<b>Connected Accounts:</b>\n"
            for acc in accounts:
                ws_health = "✅" if ws_status.get(str(acc["id"])) else "❌"
                message += (
                    f"• {acc['exchange'].upper()} - {acc['name']} "
                    f"[WS: {ws_health}]\n"
                )

        # Add back button
        keyboard = [[InlineKeyboardButton(
            "◀️ Back to Bot List",
            callback_data="status_list"
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        
        logger.info(
            "Status callback processed",
            extra={
                "bot_id": bot_id,
                "account_count": len(accounts)
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "callback": "status",
                "data": update.callback_query.data if update.callback_query else None
            },
            log_message="Error processing status callback"
        )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /balance command with enhanced error handling."""
    try:
        if not await validate_chat(update):
            return

        # Get active accounts via reference manager
        accounts = await reference_manager.get_references(
            source_type="TelegramService",
            reference_type="Account",
            filter={"is_active": True}
        )

        balance_lines = []
        total_balance = Decimal('0')
        
        for account in accounts:
            try:
                operations = ExchangeOperations(account)
                balance_info = await operations.get_balance()
                
                balance = Decimal(str(balance_info['balance']))
                equity = Decimal(str(balance_info['equity']))
                total_balance += balance
                
                balance_lines.append(
                    f"💰 <b>{account['exchange'].upper()}</b>\n"
                    f"Balance: {float(balance):.2f} USDT\n"
                    f"Equity: {float(equity):.2f} USDT\n"
                    f"Active: {'✅' if account['is_active'] else '❌'}\n"
                )
                
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "account_id": str(account['id']),
                        "command": "balance"
                    },
                    log_message="Error fetching account balance"
                )
                balance_lines.append(
                    f"❌ <b>{account['exchange'].upper()}</b>\n"
                    f"Error fetching balance\n"
                )

        if balance_lines:
            message = (
                f"📊 <b>Account Balances</b>\n\n"
                f"{''.join(balance_lines)}\n"
                f"Total Balance: {float(total_balance):.2f} USDT"
            )
            await update.message.reply_text(message, parse_mode="HTML")
        else:
            await update.message.reply_text("No active accounts found")
            
        logger.info(
            "Balance command processed",
            extra={
                "chat_id": update.effective_chat.id,
                "account_count": len(accounts)
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"command": "balance"},
            log_message="Error processing balance command"
        )

async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /performance command with enhanced error handling."""
    try:
        if not await validate_chat(update):
            return

        # Get active accounts
        accounts = await reference_manager.get_references(
            source_type="TelegramService", 
            reference_type="Account",
            filter={"is_active": True}
        )

        perf_lines = []
        total_pnl = Decimal('0')
        total_trades = 0
        
        for account in accounts:
            try:
                metrics = await performance_service.get_account_metrics(
                    account_id=str(account['id']),
                    date=datetime.utcnow()
                )
                
                total_pnl += Decimal(str(metrics['total_pnl']))
                total_trades += metrics['total_trades']
                
                perf_lines.append(
                    f"📈 <b>{account['exchange'].upper()}</b>\n"
                    f"PnL: {float(metrics['total_pnl']):.2f} USD\n"
                    f"Trades: {metrics['total_trades']}\n"
                    f"Win Rate: {metrics['win_rate']:.1f}%\n"
                    f"ROI: {metrics['roi']:.2f}%\n"
                )
                
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "account_id": str(account['id']),
                        "command": "performance"
                    },
                    log_message="Error fetching performance metrics"
                )
                perf_lines.append(
                    f"❌ <b>{account['exchange'].upper()}</b>\n"
                    f"Error fetching performance\n"
                )

        if perf_lines:
            message = (
                f"📊 <b>Today's Performance</b>\n\n"
                f"{''.join(perf_lines)}\n"
                f"Total PnL: {float(total_pnl):.2f} USD\n"
                f"Total Trades: {total_trades}"
            )
            await update.message.reply_text(message, parse_mode="HTML")
        else:
            await update.message.reply_text("No active accounts found")
            
        logger.info(
            "Performance command processed",
            extra={
                "chat_id": update.effective_chat.id,
                "account_count": len(accounts)
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"command": "performance"},
            log_message="Error processing performance command"
        )

async def handle_status_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button to return to bot list."""
    try:
        query = update.callback_query
        await query.answer()
        
        # Reuse status command to show bot list
        await status_command(update, context)
        
        logger.info(
            "Status list callback processed",
            extra={"chat_id": update.effective_chat.id}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"callback": "status_list"},
            log_message="Error processing status list callback"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command with error handling."""
    try:
        if not await validate_chat(update):
            return
            
        message = (
            "🤖 <b>Trading Bot Manager Commands</b>\n\n"
            "/status - Get current status of all bots\n"
            "/balance - Get current balance of all accounts\n"
            "/performance - Get today's trading performance\n"
            "/help - Show this help message"
        )
        
        await update.message.reply_text(message, parse_mode="HTML")
        logger.info("Help command processed", extra={"chat_id": update.effective_chat.id})

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"command": "help"},
            log_message="Error processing help command"
        )

def register_handlers(app):
    """Register all command and callback handlers."""
    try:
        app.add_handler(CommandHandler('start', start_command))
        app.add_handler(CommandHandler('status', status_command))
        app.add_handler(CommandHandler('balance', balance_command))
        app.add_handler(CommandHandler('performance', performance_command))
        app.add_handler(CommandHandler('help', help_command))
        
        # Add callback handlers
        app.add_handler(CallbackQueryHandler(
            handle_status_callback,
            pattern="^status_[0-9a-f]+$"
        ))
        app.add_handler(CallbackQueryHandler(
            handle_status_list_callback,
            pattern="^status_list$"
        ))
        
        logger.info("Telegram handlers registered successfully")

    except Exception as e:
        logger.error(
            "Failed to register handlers",
            extra={"error": str(e)}
        )
        raise

# Import at end to avoid circular imports
from decimal import Decimal
from telegram.ext import CommandHandler, CallbackQueryHandler
from app.core.errors import ValidationError
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service