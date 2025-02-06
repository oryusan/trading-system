"""
Symbol information CRUD operations with enhanced error handling.

Features:
- Symbol normalization and mapping
- Exchange-specific symbols
- Service integration
- Error handling with rich context
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.symbol_info import SymbolInfo
from app.models.symbol_specs import SymbolSpecs
from app.core.errors import (
    DatabaseError,
    ValidationError,
    NotFoundError
)
from app.core.references import ExchangeType
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

class SymbolInfoCreate(BaseModel):
    """Schema for creating symbol normalization record."""
    symbol: str                # Raw symbol (e.g., "BTC")
    normalized_symbol: str     # Exchange symbol (e.g., "BTCUSDT")
    exchange: ExchangeType

    @field_validator("symbol", "normalized_symbol")
    @classmethod
    def validate_symbol(cls, v: str, info: Any) -> str:
        """Validate symbol format."""
        if not v or not v.strip():
            raise ValidationError(
                "Symbol cannot be empty",
                context={
                    "field": info.field_name,
                    "value": v
                }
            )

        symbol = v.strip().upper()
        if info.field_name == "normalized_symbol":
            if not any(c.isdigit() for c in symbol):
                raise ValidationError(
                    "Normalized symbol must include pair",
                    context={
                        "normalized_symbol": symbol,
                        "raw_symbol": info.data.get("symbol")
                    }
                )
        return symbol

class SymbolInfoUpdate(BaseModel):
    """Schema for updating symbol mapping."""
    normalized_symbol: Optional[str] = None

class CRUDSymbolInfo(CRUDBase[SymbolInfo, SymbolInfoCreate, SymbolInfoUpdate]):
    """
    CRUD operations for symbol normalization.
    
    Features:
    - Symbol mapping management
    - Exchange normalization
    - Integration with symbol specifications
    """

    async def get_symbol_info(
        self,
        symbol: str,
        exchange: ExchangeType,
        max_age: Optional[timedelta] = None
    ) -> Optional[SymbolInfo]:
        """
        Get symbol normalization info.
        
        Args:
            symbol: Raw symbol (e.g., "BTC")
            exchange: Target exchange
            max_age: Optional maximum age for cached data
            
        Returns:
            Optional[SymbolInfo]: Symbol mapping if found
            
        Raises:
            DatabaseError: If lookup fails
        """
        try:
            query = {
                "symbol": symbol.upper(),
                "exchange": exchange
            }
            
            if max_age:
                min_timestamp = datetime.utcnow() - max_age
                query["last_updated"] = {"$gte": min_timestamp}
                
            symbol_info = await SymbolInfo.find_one(query)

            if symbol_info:
                logger.debug(
                    "Retrieved symbol info",
                    extra={
                        "symbol": symbol,
                        "exchange": exchange,
                        "normalized": symbol_info.normalized_symbol
                    }
                )
            
            return symbol_info

        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve symbol info",
                context={
                    "symbol": symbol,
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def get_normalized_symbol(
        self,
        raw_symbol: str,
        exchange: ExchangeType
    ) -> Optional[str]:
        """
        Get exchange-specific normalized symbol.
        
        Args:
            raw_symbol: Raw symbol (e.g., "BTC")
            exchange: Target exchange
            
        Returns:
            Optional[str]: Normalized symbol (e.g., "BTCUSDT")
            
        Raises:
            DatabaseError: If lookup fails
        """
        try:
            symbol_info = await self.get_symbol_info(raw_symbol, exchange)
            return symbol_info.normalized_symbol if symbol_info else None

        except Exception as e:
            raise DatabaseError(
                "Failed to get normalized symbol",
                context={
                    "raw_symbol": raw_symbol,
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def update_or_create(
        self,
        symbol: str,
        normalized_symbol: str,
        exchange: ExchangeType
    ) -> SymbolInfo:
        """
        Update or create symbol mapping.
        
        Args:
            symbol: Raw symbol
            normalized_symbol: Exchange-specific symbol
            exchange: Target exchange
            
        Returns:
            SymbolInfo: Updated or created mapping
            
        Raises:
            ValidationError: If symbols invalid
            DatabaseError: If operation fails
        """
        try:
            existing = await self.get_symbol_info(symbol, exchange)
            
            if existing:
                existing.normalized_symbol = normalized_symbol.upper()
                existing.last_updated = datetime.utcnow()
                await existing.save()
                
                logger.info(
                    "Updated symbol mapping",
                    extra={
                        "symbol": symbol,
                        "normalized": normalized_symbol,
                        "exchange": exchange
                    }
                )
                return existing
            
            new_symbol_info = SymbolInfo(
                symbol=symbol.upper(),
                normalized_symbol=normalized_symbol.upper(),
                exchange=exchange,
                last_updated=datetime.utcnow()
            )
            await new_symbol_info.insert()
            
            logger.info(
                "Created new symbol mapping",
                extra={
                    "symbol": symbol,
                    "normalized": normalized_symbol,
                    "exchange": exchange
                }
            )
            return new_symbol_info

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update/create symbol mapping",
                context={
                    "symbol": symbol,
                    "normalized": normalized_symbol,
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def get_all_by_exchange(
        self,
        exchange: ExchangeType
    ) -> List[SymbolInfo]:
        """
        Get all symbol mappings for an exchange.
        
        Args:
            exchange: Exchange to get symbols for
            
        Returns:
            List[SymbolInfo]: All symbol mappings
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            symbols = await SymbolInfo.find({"exchange": exchange}).to_list()
            
            logger.debug(
                "Retrieved symbols for exchange",
                extra={
                    "exchange": exchange,
                    "count": len(symbols)
                }
            )
            return symbols

        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve symbols for exchange",
                context={
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def get_all_symbols(self) -> Dict[ExchangeType, Dict[str, str]]:
        """
        Get all symbol mappings grouped by exchange.
        
        Returns:
            Dict[ExchangeType, Dict[str, str]]: Mapping of:
            exchange -> {raw_symbol -> normalized_symbol}
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            all_symbols = await SymbolInfo.find_all().to_list()
            
            result: Dict[ExchangeType, Dict[str, str]] = {}
            for symbol_info in all_symbols:
                if symbol_info.exchange not in result:
                    result[symbol_info.exchange] = {}
                result[symbol_info.exchange][symbol_info.symbol] = symbol_info.normalized_symbol
            
            logger.debug(
                "Retrieved all symbol mappings",
                extra={
                    "exchange_count": len(result),
                    "total_symbols": sum(len(symbols) for symbols in result.values())
                }
            )
            return result

        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve all symbols",
                context={"error": str(e)}
            )

    async def validate_symbol(
        self,
        symbol: str,
        exchange: ExchangeType
    ) -> bool:
        """
        Validate symbol exists and has specifications.
        
        Checks both symbol mapping and trading specifications.
        
        Args:
            symbol: Raw symbol to validate
            exchange: Target exchange
            
        Returns:
            bool: True if symbol valid and has specifications
            
        Raises:
            DatabaseError: If validation fails
        """
        try:
            # Check symbol mapping exists
            symbol_info = await self.get_symbol_info(symbol, exchange)
            if not symbol_info:
                return False
                
            # Check specifications exist
            specs = await SymbolSpecs.find_one({
                "symbol": symbol_info.normalized_symbol,
                "exchange": exchange
            })
            
            is_valid = specs is not None
            logger.debug(
                "Symbol validation result",
                extra={
                    "symbol": symbol,
                    "normalized": symbol_info.normalized_symbol if symbol_info else None,
                    "exchange": exchange,
                    "is_valid": is_valid
                }
            )
            return is_valid

        except Exception as e:
            raise DatabaseError(
                "Failed to validate symbol",
                context={
                    "symbol": symbol,
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def clean_old_data(self, max_age: timedelta) -> int:
        """
        Delete symbol mappings older than max_age.
        
        Args:
            max_age: Maximum age of records to keep
            
        Returns:
            int: Number of records deleted
            
        Raises:
            DatabaseError: If cleanup fails
        """
        try:
            min_timestamp = datetime.utcnow() - max_age
            result = await SymbolInfo.delete_many({
                "last_updated": {"$lt": min_timestamp}
            })
            
            deleted_count = result.deleted_count
            logger.info(
                "Cleaned old symbol records",
                extra={
                    "deleted_count": deleted_count,
                    "max_age": str(max_age)
                }
            )
            return deleted_count

        except Exception as e:
            raise DatabaseError(
                "Failed to clean old symbol data",
                context={
                    "max_age": str(max_age),
                    "error": str(e)
                }
            )

    async def bulk_update(
        self,
        exchange: ExchangeType,
        symbols_data: List[Dict[str, str]]
    ) -> List[SymbolInfo]:
        """
        Bulk update or create symbol mappings.
        
        Args:
            exchange: Target exchange 
            symbols_data: List of {symbol, normalized_symbol} dicts
            
        Returns:
            List[SymbolInfo]: Updated/created mappings
            
        Raises:
            ValidationError: If data invalid
            DatabaseError: If operation fails
        """
        try:
            if not symbols_data:
                raise ValidationError(
                    "Symbols data cannot be empty",
                    context={"exchange": exchange}
                )

            updated_symbols = []
            errors = []

            for data in symbols_data:
                try:
                    if not all(k in data for k in ["symbol", "normalized_symbol"]):
                        raise ValidationError(
                            "Invalid symbol data format",
                            context={"data": data}
                        )

                    symbol_info = await self.update_or_create(
                        symbol=data["symbol"],
                        normalized_symbol=data["normalized_symbol"],
                        exchange=exchange
                    )
                    updated_symbols.append(symbol_info)
                
                except (ValidationError, ValueError) as e:
                    errors.append({
                        "symbol": data.get("symbol", "unknown"),
                        "error": str(e)
                    })
                    continue

            if errors:
                logger.warning(
                    "Some symbols failed to update",
                    extra={
                        "success_count": len(updated_symbols),
                        "error_count": len(errors),
                        "errors": errors
                    }
                )

            logger.info(
                "Bulk updated symbols",
                extra={
                    "exchange": exchange,
                    "updated_count": len(updated_symbols)
                }
            )
            
            return updated_symbols

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to perform bulk update",
                context={
                    "exchange": exchange,
                    "symbol_count": len(symbols_data),
                    "error": str(e)
                }
            )

# Create singleton instance
symbol_info = CRUDSymbolInfo(SymbolInfo)