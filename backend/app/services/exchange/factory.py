"""
Exchange factory implementation with symbol validation and instance management.

Features:
- Exchange instance creation and caching
- Symbol validation and normalization
- Resource cleanup
- Error handling
"""

from typing import Dict, Optional, Any, Type
from datetime import datetime, timedelta
from decimal import Decimal
import asyncio

class ExchangeFactory:
    """
    Factory for managing exchange instances and resources.
    
    Features:
    - Instance lifecycle management
    - Resource cleanup
    - Error handling
    """
    
    _instances: Dict[str, ExchangeProtocol] = {}
    _last_used: Dict[str, datetime] = {}
    _cleanup_lock = asyncio.Lock()
    INSTANCE_TIMEOUT = timedelta(hours=1)

    @classmethod
    async def get_instance(
        cls,
        account_id: str,
        reference_manager: ReferenceManagerProtocol
    ) -> ExchangeProtocol:
        """
        Get or create exchange instance with validation.
        
        Args:
            account_id: Account to get exchange for
            reference_manager: Reference manager for lookups
            
        Returns:
            ExchangeProtocol: Exchange instance
            
        Raises:
            ConfigurationError: If credentials invalid
            ExchangeError: If instance creation fails
        """
        current_time = datetime.utcnow()
        
        try:
            # Return cached instance if available
            if account_id in cls._instances:
                cls._last_used[account_id] = current_time
                return cls._instances[account_id]

            # Get account details
            account = await reference_manager.get_reference(account_id)
            if not account:
                raise ConfigurationError(
                    "Account not found",
                    context={"account_id": account_id}
                )

            # Validate credentials
            credentials = await cls._validate_credentials(account)

            # Get exchange implementation
            exchange_class = cls._get_exchange_class(account["exchange"])
            if not exchange_class:
                raise ConfigurationError(
                    "Unsupported exchange",
                    context={"exchange": account["exchange"]}
                )

            # Create and connect instance
            exchange = exchange_class(credentials)
            await exchange.connect()

            # Cache instance
            cls._instances[account_id] = exchange
            cls._last_used[account_id] = current_time

            logger.info(
                "Created exchange instance",
                extra={
                    "account_id": account_id,
                    "exchange": account["exchange"]
                }
            )

            return exchange

        except (ConfigurationError, ExchangeError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get exchange instance",
                context={
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    @classmethod
    async def _validate_credentials(
        cls,
        account: Dict[str, Any]
    ) -> ExchangeCredentials:
        """
        Validate account credentials.
        
        Args:
            account: Account details containing credentials
            
        Returns:
            ExchangeCredentials: Validated credentials
            
        Raises:
            ConfigurationError: If credentials invalid
        """
        if not account.get("api_key") or not account.get("api_secret"):
            raise ConfigurationError(
                "Invalid exchange credentials",
                context={
                    "account_id": account.get("id"),
                    "exchange": account.get("exchange")
                }
            )

        return ExchangeCredentials(
            api_key=account["api_key"],
            api_secret=account["api_secret"],
            passphrase=account.get("passphrase"),
            testnet=account.get("is_testnet", False)
        )

    @classmethod
    def _get_exchange_class(
        cls,
        exchange_type: str
    ) -> Optional[Type[ExchangeProtocol]]:
        """Get exchange class implementation."""
        exchange_map = {
            "okx": OKXExchange,
            "bybit": BybitExchange,
            "bitget": BitgetExchange
        }
        return exchange_map.get(exchange_type)

    @classmethod
    async def cleanup_instances(cls) -> None:
        """Clean up unused instances with resource management."""
        try:
            async with cls._cleanup_lock:
                current_time = datetime.utcnow()
                to_remove = []

                # Find stale instances
                for account_id, last_used in cls._last_used.items():
                    if current_time - last_used > cls.INSTANCE_TIMEOUT:
                        to_remove.append(account_id)

                # Close and remove instances
                for account_id in to_remove:
                    try:
                        instance = cls._instances.pop(account_id)
                        await instance.close()
                        cls._last_used.pop(account_id)
                    except Exception as e:
                        logger.error(
                            "Failed to cleanup instance",
                            extra={
                                "account_id": account_id,
                                "error": str(e)
                            }
                        )

                if to_remove:
                    logger.info(
                        "Cleaned up exchange instances",
                        extra={"removed_count": len(to_remove)}
                    )

        except Exception as e:
            logger.error(
                "Instance cleanup failed",
                extra={"error": str(e)}
            )

class SymbolValidator:
    """
    Symbol validation with caching.
    
    Features:
    - Symbol validation and normalization
    - Specification caching
    - CCXT integration
    - Resource cleanup
    """
    
    def __init__(self):
        """Initialize validator with caching."""
        self._cache: Dict[str, Dict] = {}
        self._ccxt_instances: Dict[str, "ccxt.Exchange"] = {}
        self._validation_lock = asyncio.Lock()
        self.logger = logger.getChild("symbol_validator")

    def _get_ccxt_instance(self, exchange_type: str) -> "ccxt.Exchange":
        """
        Get or create CCXT instance for exchange.
        
        Args:
            exchange_type: Exchange to get instance for
            
        Returns:
            ccxt.Exchange: CCXT exchange instance
            
        Raises:
            ConfigurationError: If instance creation fails
        """
        try:
            if exchange_type not in self._ccxt_instances:
                exchange_class = getattr(ccxt, exchange_type)
                self._ccxt_instances[exchange_type] = exchange_class({
                    "enableRateLimit": True,
                    "timeout": 30000
                })
            return self._ccxt_instances[exchange_type]

        except Exception as e:
            raise ConfigurationError(
                "Failed to create CCXT instance",
                context={
                    "exchange": exchange_type,
                    "error": str(e)
                }
            )

    async def validate_symbol(
        self,
        symbol: str,
        exchange_type: str,
        force_validation: bool = False
    ) -> Dict[str, Any]:
        """
        Validate and normalize symbol for exchange.
        
        Args:
            symbol: Symbol to validate
            exchange_type: Target exchange
            force_validation: Skip cache check
            
        Returns:
            Dict containing:
            - original: Original symbol
            - normalized: Exchange format
            - specs: Trading specifications
            
        Raises:
            ValidationError: If symbol invalid
            ExchangeError: If validation fails
        """
        cache_key = f"{symbol}_{exchange_type}"
        
        try:
            # Check cache unless forced
            if not force_validation and cache_key in self._cache:
                return self._cache[cache_key]
                
            async with self._validation_lock:
                # Get specifications from database
                specs = await SymbolSpecs.find_one({
                    "symbol": symbol.upper(),
                    "exchange": exchange_type,
                    "is_active": True
                })
                
                if not specs:
                    # Get CCXT instance for validation
                    ccxt_client = self._get_ccxt_instance(exchange_type)
                    await ccxt_client.load_markets()
                    
                    # Normalize symbol
                    try:
                        market_symbol = ccxt_client.market_id(f"{symbol}/USDT")
                        market_info = ccxt_client.market(market_symbol)
                        
                        symbol_info = {
                            "tick_size": Decimal(str(market_info.get("precision", {}).get("price", 0.1))),
                            "lot_size": Decimal(str(market_info.get("precision", {}).get("amount", 0.001))),
                            "contract_size": Decimal(str(market_info.get("contractSize", 1)))
                        }
                    except Exception as e:
                        raise ValidationError(
                            "Symbol normalization failed",
                            context={
                                "symbol": symbol,
                                "exchange": exchange_type,
                                "error": str(e)
                            }
                        )
                    
                    # Create specs record
                    specs = await SymbolSpecs.get_or_create(
                        symbol=symbol.upper(),
                        exchange=exchange_type,
                        **symbol_info
                    )
                    
                # Create validation result
                result = {
                    "original": symbol,
                    "normalized": specs.symbol,
                    "specifications": {
                        "tick_size": str(specs.tick_size),
                        "lot_size": str(specs.lot_size),
                        "contract_size": str(specs.contract_size)
                    },
                    "timestamp": datetime.utcnow()
                }
                
                # Update cache
                self._cache[cache_key] = result
                
                self.logger.info(
                    "Validated symbol",
                    extra={
                        "symbol": symbol,
                        "exchange": exchange_type,
                        "normalized": specs.symbol
                    }
                )
                
                return result
                
        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            raise ExchangeError(
                "Symbol validation failed",
                context={
                    "symbol": symbol,
                    "exchange": exchange_type,
                    "error": str(e)
                }
            )

    async def invalidate_cache(
        self,
        symbol: Optional[str] = None,
        exchange_type: Optional[str] = None
    ) -> None:
        """
        Invalidate symbol cache entries.
        
        Args:
            symbol: Optional symbol to invalidate
            exchange_type: Optional exchange to invalidate
            
        If neither provided, clears entire cache.
        """
        try:
            if symbol and exchange_type:
                cache_key = f"{symbol}_{exchange_type}"
                self._cache.pop(cache_key, None)
                self.logger.info(
                    "Invalidated cache entry",
                    extra={
                        "symbol": symbol,
                        "exchange": exchange_type
                    }
                )
            else:
                self._cache.clear()
                self.logger.info("Cleared symbol cache")

        except Exception as e:
            self.logger.error(
                "Failed to invalidate cache",
                extra={
                    "symbol": symbol,
                    "exchange": exchange_type,
                    "error": str(e)
                }
            )

    async def close(self) -> None:
        """Close CCXT instances and cleanup resources."""
        try:
            for exchange_id, exchange in self._ccxt_instances.items():
                try:
                    await exchange.close()
                except Exception as e:
                    self.logger.error(
                        f"Error closing CCXT instance for {exchange_id}",
                        extra={"error": str(e)}
                    )
            self._ccxt_instances.clear()
            self.logger.info("Closed all CCXT instances")

        except Exception as e:
            raise ExchangeError(
                "Failed to close CCXT instances",
                context={"error": str(e)}
            )

# Move imports to end to avoid circular imports
import ccxt
from app.core.errors import (
    ExchangeError,
    ValidationError,
    ConfigurationError,
    DatabaseError
)
from app.core.logging.logger import get_logger
from app.models.symbol_specs import SymbolSpecs
from app.services.reference.manager import ReferenceManagerProtocol
from .base import ExchangeCredentials, ExchangeProtocol
from .exchanges import OKXExchange, BybitExchange, BitgetExchange

logger = get_logger(__name__)

# Create singleton instances
exchange_factory = ExchangeFactory()
symbol_validator = SymbolValidator()