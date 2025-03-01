"""
This module aggregates and re-exports all CRUD objects from the /crud directory.

By importing from `app.crud`, other parts of the application can conveniently access all
CRUD instances for users, bots, accounts, groups, trades, and symbol information without
needing to import each one individually.

Exports:
    user: CRUD instance for User model operations (create, read, update, delete users).
    bot: CRUD instance for Bot model operations (manage bots, connect accounts, update statuses).
    account: CRUD instance for Account model operations (manage trading accounts, balances, bots, and groups).
    group: CRUD instance for AccountGroup model operations (manage groups, performance, assigned accounts).
    trade: CRUD instance for Trade model operations (track and aggregate trade history and performance).
    symbol: CRUD instance for SymbolData model operations (manage symbol normalization and info).
"""

from .crud_user import user
from .crud_bot import bot
from .crud_account import account
from .crud_group import group
from .crud_trade import trade
from .crud_symbol import symbol

__all__ = [
    "user",
    "bot",
    "account",
    "group",
    "trade",
    "symbol"
]
