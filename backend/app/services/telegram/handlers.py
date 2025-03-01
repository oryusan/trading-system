"""
Telegram bot command handlers with enhanced error handling and service integration.

Features:
- Command handlers with inline keyboard support
- Enhanced error handling and logging
- Service integration (trading, reference manager)
- WebSocket status monitoring
"""

# Standard library
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Callable, Optional, Dict

# Third-party libraries
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

# Local modules
from app.core.config.settings import settings
from app.core.errors.base import ValidationError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.services.exchange.operations import ExchangeOperations
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager

logger = get_logger(__name__)


async def validate_chat(update: Update) -> bool:
    """Validate chat ID matches configured ID."""
    if str(update.effective_chat.id) != settings.telegram.TELEGRAM_CHAT_ID:
        await handle_api_error(
            error=ValidationError(
                "Invalid chat ID",
                context={
                    "chat_id": str(update.effective_chat.id),
                    "expected": settings.telegram.TELEGRAM_CHAT_ID,
                },
            ),
            context={"command": "validate_chat"},
            log_message="Invalid Telegram chat ID",
        )
        return False
    return True


def handle_errors(
    name: str,
    log_message: str,
    check_chat: bool = True,
    extra_context: Optional[Callable[[Update], Dict]] = None,
):
    """
    Decorator to handle errors in Telegram command and callback handlers.

    :param name: The command or callback name.
    :param log_message: Log message in case of errors.
    :param check_chat: If True, validates the chat ID using `validate_chat`.
    :param extra_context: Optional callable that extracts extra context from the update.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            if check_chat and update.message and not await validate_chat(update):
                return
            try:
                return await func(update, context, *args, **kwargs)
            except Exception as e:
                error_context = {"command": name}
                if extra_context:
                    error_context.update(extra_context(update))
                await handle_api_error(
                    error=e,
                    context=error_context,
                    log_message=log_message,
                )
        return wrapper
    return decorator


@handle_errors("start", "Error processing start command")
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
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


@handle_errors("status", "Error processing status command")
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /status command."""
    bots = await reference_manager.get_references(
        source_type="TelegramService",
        reference_type="Bot",
    )
    if not bots:
        await update.message.reply_text("No bots found")
        return

    keyboard = []
    row = []
    for idx, bot in enumerate(bots, 1):
        row.append(
            InlineKeyboardButton(
                text=bot["name"],
                callback_data=f"status_{bot['id']}",
            )
        )
        if idx % 3 == 0 or idx == len(bots):
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📊 Select a bot to view status:", reply_markup=reply_markup)
    logger.info(
        "Status command processed",
        extra={"chat_id": update.effective_chat.id, "bot_count": len(bots)},
    )


@handle_errors(
    "status_callback",
    "Error processing status callback",
    check_chat=False,
    extra_context=lambda update: {"data": update.callback_query.data if update.callback_query else None},
)
async def handle_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot selection callback."""
    query = update.callback_query
    await query.answer()
    bot_id = query.data.split("_")[1]
    bot = await reference_manager.get_reference(bot_id)
    if not bot:
        await query.edit_message_text("Bot not found")
        return

    accounts = await reference_manager.get_references(
        source_type="Bot",
        reference_id=bot_id,
    )

    ws_status = {}
    for account in accounts:
        ws_client = await ws_manager.get_connection(str(account["id"]))
        if ws_client:
            ws_status[str(account["id"])] = await ws_client.is_healthy()

    status_emoji = "🟢" if bot["status"] == "active" else "🔴"
    last_signal = (
        bot["last_signal"].strftime("%Y-%m-%d %H:%M:%S")
        if bot.get("last_signal")
        else "N/A"
    )
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
            message += f"• {acc['exchange'].upper()} - {acc['name']} [WS: {ws_health}]\n"

    keyboard = [[InlineKeyboardButton("◀️ Back to Bot List", callback_data="status_list")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=message, reply_markup=reply_markup, parse_mode="HTML")
    logger.info("Status callback processed", extra={"bot_id": bot_id, "account_count": len(accounts)})


@handle_errors("balance", "Error processing balance command")
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /balance command."""
    accounts = await reference_manager.get_references(
        source_type="TelegramService",
        reference_type="Account",
        filter={"is_active": True},
    )

    balance_lines = []
    total_balance = Decimal("0")
    for account in accounts:
        try:
            operations = ExchangeOperations(account)
            balance_info = await operations.get_balance()
            balance = Decimal(str(balance_info["balance"]))
            equity = Decimal(str(balance_info["equity"]))
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
                context={"account_id": str(account["id"]), "command": "balance"},
                log_message="Error fetching account balance",
            )
            balance_lines.append(
                f"❌ <b>{account['exchange'].upper()}</b>\n"
                "Error fetching balance\n"
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
        extra={"chat_id": update.effective_chat.id, "account_count": len(accounts)},
    )


@handle_errors("performance", "Error processing performance command")
async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /performance command."""
    accounts = await reference_manager.get_references(
        source_type="TelegramService",
        reference_type="Account",
        filter={"is_active": True},
    )

    perf_lines = []
    total_pnl = Decimal("0")
    total_trades = 0
    for account in accounts:
        try:
            metrics = await performance_service.get_account_metrics(
                account_id=str(account["id"]),
                date=datetime.utcnow(),
            )
            total_pnl += Decimal(str(metrics["total_pnl"]))
            total_trades += metrics["total_trades"]
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
                context={"account_id": str(account["id"]), "command": "performance"},
                log_message="Error fetching performance metrics",
            )
            perf_lines.append(
                f"❌ <b>{account['exchange'].upper()}</b>\n"
                "Error fetching performance\n"
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
        extra={"chat_id": update.effective_chat.id, "account_count": len(accounts)},
    )


@handle_errors("status_list", "Error processing status list callback", check_chat=False)
async def handle_status_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button to return to bot list."""
    query = update.callback_query
    await query.answer()
    await status_command(update, context)
    logger.info("Status list callback processed", extra={"chat_id": update.effective_chat.id})


@handle_errors("help", "Error processing help command")
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command."""
    message = (
        "🤖 <b>Trading Bot Manager Commands</b>\n\n"
        "/status - Get current status of all bots\n"
        "/balance - Get current balance of all accounts\n"
        "/performance - Get today's trading performance\n"
        "/help - Show this help message"
    )
    await update.message.reply_text(message, parse_mode="HTML")
    logger.info("Help command processed", extra={"chat_id": update.effective_chat.id})


def register_handlers(app) -> None:
    """Register all command and callback handlers."""
    try:
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("balance", balance_command))
        app.add_handler(CommandHandler("performance", performance_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CallbackQueryHandler(handle_status_callback, pattern="^status_[0-9a-f]+$"))
        app.add_handler(CallbackQueryHandler(handle_status_list_callback, pattern="^status_list$"))
        logger.info("Telegram handlers registered successfully")
    except Exception as e:
        logger.error("Failed to register handlers", extra={"error": str(e)})
        raise
