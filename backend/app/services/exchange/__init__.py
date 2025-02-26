"""
This `__init__.py` file aggregates key exchange-related services into a single import point.

Exports:
- ExchangeOperations: A high-level operations wrapper that executes trades, updates balances, and manages positions for a given account's exchange instance.
- exchange_factory: A function that returns (or creates) a connected exchange instance for a specified account, caching instances per account.

By importing from `app.services.exchange`, other parts of the application can easily access these core exchange functionalities.
"""

from app.services.exchange.operations import ExchangeOperations
from app.services.exchange.factory import exchange_factory

__all__ = ["ExchangeOperations", "exchange_factory"]
