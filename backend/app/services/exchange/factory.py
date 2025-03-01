from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional, Type

import ccxt

from app.core.errors.base import (
    ConfigurationError,
    DatabaseError,
    ExchangeError,
    ValidationError,
)
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger
from app.models.entities.symbol_data import SymbolData
from app.core.references import ReferenceManagerProtocol, ExchangeType
from .base import ExchangeCredentials, ExchangeProtocol
from app.services.exchange.exchanges.okx import OKXExchange
from app.services.exchange.exchanges.bybit import BybitExchange
from app.services.exchange.exchanges.bitget import BitgetExchange


logger = get_logger(__name__)


class ExchangeFactory:
    """
    Factory for managing exchange instances and resources.

    Features:
      - Instance lifecycle management
      - Resource cleanup
      - Error handling via decorators
    """

    _instances: Dict[str, ExchangeProtocol] = {}
    _last_used: Dict[str, datetime] = {}
    _cleanup_lock = asyncio.Lock()
    INSTANCE_TIMEOUT = timedelta(hours=1)

    @classmethod
    @error_handler(
        context_extractor=lambda cls, account_id, reference_manager: {"account_id": account_id},
        log_message="Failed to get or create exchange instance"
    )
    async def get_instance(cls, account_id: str, reference_manager: ReferenceManagerProtocol) -> ExchangeProtocol:
        """
        Get or create an exchange instance with validation.

        Args:
            account_id: The account identifier.
            reference_manager: Reference manager for lookups.

        Returns:
            The exchange instance.

        Raises:
            ConfigurationError: If credentials are invalid or the exchange is unsupported.
            ExchangeError: If exchange instance creation fails.
            DatabaseError: For any unexpected error.
        """
        current_time = datetime.utcnow()
        if account_id in cls._instances:
            cls._last_used[account_id] = current_time
            return cls._instances[account_id]

        account = await reference_manager.get_reference(account_id)
        if not account:
            raise ConfigurationError("Account not found", context={"account_id": account_id})

        credentials = await cls._validate_credentials(account)
        exchange_class = cls._get_exchange_class(account["exchange"])
        if not exchange_class:
            raise ConfigurationError("Unsupported exchange", context={"exchange": account["exchange"]})

        exchange = exchange_class(credentials)
        await exchange.connect()

        cls._instances[account_id] = exchange
        cls._last_used[account_id] = current_time

        logger.info("Created exchange instance", extra={"account_id": account_id, "exchange": account["exchange"]})
        return exchange

    @classmethod
    async def _validate_credentials(cls, account: Dict[str, Any]) -> ExchangeCredentials:
        """
        Validate account credentials.

        Args:
            account: Account details containing credentials.

        Returns:
            Validated ExchangeCredentials.

        Raises:
            ConfigurationError: If credentials are missing or invalid.
        """
        if not account.get("api_key") or not account.get("api_secret"):
            raise ConfigurationError(
                "Invalid exchange credentials",
                context={"account_id": account.get("id"), "exchange": account.get("exchange")}
            )
        return ExchangeCredentials(
            api_key=account["api_key"],
            api_secret=account["api_secret"],
            passphrase=account.get("passphrase"),
            testnet=account.get("is_testnet", False),
        )

    @classmethod
    def _get_exchange_class(cls, exchange_type: str) -> Optional[Type[ExchangeProtocol]]:
        """
        Get the exchange class implementation for the given exchange type.
        """
        exchange_map: Dict[str, Type[ExchangeProtocol]] = {
            "okx": OKXExchange,
            "bybit": BybitExchange,
            "bitget": BitgetExchange,
        }
        return exchange_map.get(exchange_type)

    @classmethod
    @error_handler(
        context_extractor=lambda cls: {"action": "cleanup_instances"},
        log_message="Failed to cleanup unused exchange instances"
    )
    async def cleanup_instances(cls) -> None:
        """
        Clean up unused exchange instances that have been idle past the timeout.
        """
        async with cls._cleanup_lock:
            current_time = datetime.utcnow()
            stale_accounts = [
                account_id
                for account_id, last_used in cls._last_used.items()
                if current_time - last_used > cls.INSTANCE_TIMEOUT
            ]

            for account_id in stale_accounts:
                try:
                    instance = cls._instances.pop(account_id, None)
                    if instance:
                        await instance.close()
                    cls._last_used.pop(account_id, None)
                except Exception as e:
                    logger.error("Failed to cleanup instance", extra={"account_id": account_id, "error": str(e)})
            if stale_accounts:
                logger.info("Cleaned up exchange instances", extra={"removed_count": len(stale_accounts)})

class SymbolValidator:
    """
    Symbol validation with caching.

    Features:
      - Symbol validation and normalization
      - Specification caching
      - CCXT integration
      - Resource cleanup
      - Global error handling via decorators
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ccxt_instances: Dict[str, ccxt.Exchange] = {}
        self._validation_lock = asyncio.Lock()
        self.logger = get_logger("symbol_validator")

    def _get_ccxt_instance(self, exchange_type: str) -> ccxt.Exchange:
        try:
            if exchange_type not in self._ccxt_instances:
                exchange_class = getattr(ccxt, exchange_type)
                self._ccxt_instances[exchange_type] = exchange_class(
                    {"enableRateLimit": True, "timeout": 30000}
                )
            return self._ccxt_instances[exchange_type]
        except Exception as e:
            raise ConfigurationError(
                "Failed to create CCXT instance",
                context={"exchange": exchange_type, "error": str(e)},
            ) from e

    @error_handler(
        context_extractor=lambda self, symbol, exchange_type, force_validation=False: {"symbol": symbol, "exchange": exchange_type},
        log_message="Symbol validation failed"
    )
    async def validate_symbol(
        self, symbol: str, exchange_type: str, force_validation: bool = False
    ) -> Dict[str, Any]:
        """
        Validate and normalize a symbol for a specific exchange.

        Args:
            symbol: The symbol to validate.
            exchange_type: The target exchange.
            force_validation: Whether to skip the cache check.

        Returns:
            A dictionary containing:
              - original: The original symbol.
              - normalized: The exchange-formatted symbol.
              - specifications: Trading specifications.
              - timestamp: The validation timestamp.
        """
        cache_key = f"{symbol}_{exchange_type}"
        if not force_validation and cache_key in self._cache:
            return self._cache[cache_key]

        async with self._validation_lock:
            specs = await SymbolData.find_one({
                "symbol": symbol.upper(),
                "exchange": exchange_type,
                "is_active": True
            })
            if not specs:
                ccxt_client = self._get_ccxt_instance(exchange_type)
                await ccxt_client.load_markets()
                try:
                    market_symbol = ccxt_client.market_id(f"{symbol}/USDT")
                    market_info = ccxt_client.market(market_symbol)
                    symbol_info = {
                        "tick_size": Decimal(str(market_info.get("precision", {}).get("price", 0.1))),
                        "lot_size": Decimal(str(market_info.get("precision", {}).get("amount", 0.001))),
                        "contract_size": Decimal(str(market_info.get("contractSize", 1))),
                    }
                except Exception as e:
                    raise ValidationError(
                        "Symbol normalization failed",
                        context={"symbol": symbol, "exchange": exchange_type, "error": str(e)}
                    ) from e

                specs = await SymbolData.get_or_create(
                    original_symbol=symbol,
                    symbol=symbol.upper(),
                    exchange=exchange_type,
                    **symbol_info
                )

            result = {
                "original": symbol,
                "normalized": specs.symbol,
                "specifications": {
                    "tick_size": str(specs.tick_size),
                    "lot_size": str(specs.lot_size),
                    "contract_size": str(specs.contract_size),
                },
                "timestamp": datetime.utcnow(),
            }
            self._cache[cache_key] = result
            self.logger.info("Validated symbol", extra={"symbol": symbol, "exchange": exchange_type, "normalized": specs.symbol})
            return result

    @error_handler(
        context_extractor=lambda self, symbol=None, exchange_type=None: {"symbol": symbol, "exchange": exchange_type} if symbol and exchange_type else {},
        log_message="Failed to invalidate symbol cache"
    )
    async def invalidate_cache(self, symbol: Optional[str] = None, exchange_type: Optional[str] = None) -> None:
        """
        Invalidate symbol cache entries.

        If both symbol and exchange_type are provided, only that entry is invalidated.
        Otherwise, the entire cache is cleared.
        """
        if symbol and exchange_type:
            cache_key = f"{symbol}_{exchange_type}"
            self._cache.pop(cache_key, None)
            self.logger.info("Invalidated cache entry", extra={"symbol": symbol, "exchange": exchange_type})
        else:
            self._cache.clear()
            self.logger.info("Cleared symbol cache")


# Global instances for use throughout the application.
exchange_factory = ExchangeFactory()
symbol_validator = SymbolValidator()
