"""
This `__init__.py` file aggregates Telegram-related services and handlers into a single import point.

Exports:
- telegram_bot: The TelegramService singleton for sending notifications and processing messages.
- start_command, status_command, balance_command, performance_command, help_command:
  Handlers for responding to Telegram commands related to starting, viewing status, checking balances and performance, and showing help.
- register_handlers: A function to register all command handlers with the Telegram application.

By importing from `app.services.telegram`, other parts of the application can easily integrate the Telegram bot service and its commands.
"""

from app.services.telegram.service import telegram_bot
from app.services.telegram.handlers import (
    start_command,
    status_command,
    balance_command,
    performance_command,
    help_command,
    register_handlers
)

__all__ = [
    "telegram_bot",
    "start_command",
    "status_command",
    "balance_command",
    "performance_command",
    "help_command",
    "register_handlers"
]
