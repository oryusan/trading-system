"""
Symbol information CRUD operations with enhanced error handling.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator
from decimal import Decimal

from app.crud.crud_base import CRUDBase
from app.models.entities.symbol_info import SymbolInfo
from app.models.entities.symbol_specs import SymbolSpecs
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.references import ExchangeType
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

logger = get_logger(__name__)

def normalize_symbol(symbol: str) -> str:
    """Helper function to strip whitespace and convert a symbol to uppercase."""
    return symbol.strip().upper()

class SymbolInfoCreate(BaseModel):
    """Schema for creating a symbol normalization record."""
    symbol: str
    normalized_symbol: str
    exchange: ExchangeType

    @field_validator("symbol", "normalized_symbol")
    @classmethod
    def validate_symbol(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValidationError(
                "Symbol cannot be empty",
                context={"field": info.field_name, "value": v}
            )
        normalized = normalize_symbol(v)
        if info.field_name == "normalized_symbol" and not any(c.isdigit() for c in normalized):
            raise ValidationError(
                "Normalized symbol must include pair",
                context={
                    "normalized_symbol": normalized,
                    "raw_symbol": info.data.get("symbol")
                }
            )
        return normalized

class SymbolInfoUpdate(BaseModel):
    """Schema for updating symbol mapping."""
    normalized_symbol: Optional[str] = None

class CRUDSymbolInfo(CRUDBase[SymbolInfo, SymbolInfoCreate, SymbolInfoUpdate]):
    """
    CRUD operations for symbol normalization.
    """

    @handle_db_error("Failed to retrieve symbol info", lambda self, symbol, exchange, max_age=None: {"symbol": symbol, "exchange": exchange, "max_age": str(max_age) if max_age else None})
    async def get_symbol_info(
        self,
        symbol: str,
        exchange: ExchangeType,
        max_age: Optional[timedelta] = None
    ) -> Optional[SymbolInfo]:
        query: Dict[str, Any] = {
            "symbol": normalize_symbol(symbol),
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

    @handle_db_error("Failed to get normalized symbol", lambda self, raw_symbol, exchange: {"raw_symbol": raw_symbol, "exchange": exchange})
    async def get_normalized_symbol(
        self,
        raw_symbol: str,
        exchange: ExchangeType
    ) -> Optional[str]:
        symbol_info = await self.get_symbol_info(raw_symbol, exchange)
        return symbol_info.normalized_symbol if symbol_info else None

    @handle_db_error("Failed to update/create symbol mapping", lambda self, symbol, normalized_symbol, exchange: {"symbol": symbol, "normalized": normalized_symbol, "exchange": exchange})
    async def update_or_create(
        self,
        symbol: str,
        normalized_symbol: str,
        exchange: ExchangeType
    ) -> SymbolInfo:
        norm_symbol = normalize_symbol(symbol)
        norm_normalized = normalize_symbol(normalized_symbol)
        existing = await self.get_symbol_info(norm_symbol, exchange)
        current_time = datetime.utcnow()
        if existing:
            existing.normalized_symbol = norm_normalized
            existing.last_updated = current_time
            await existing.save()
            logger.info(
                "Updated symbol mapping",
                extra={
                    "symbol": norm_symbol,
                    "normalized": norm_normalized,
                    "exchange": exchange
                }
            )
            return existing
        new_symbol_info = SymbolInfo(
            symbol=norm_symbol,
            normalized_symbol=norm_normalized,
            exchange=exchange,
            last_updated=current_time
        )
        await new_symbol_info.insert()
        logger.info(
            "Created new symbol mapping",
            extra={
                "symbol": norm_symbol,
                "normalized": norm_normalized,
                "exchange": exchange
            }
        )
        return new_symbol_info

    @handle_db_error("Failed to retrieve symbols for exchange", lambda self, exchange: {"exchange": exchange})
    async def get_all_by_exchange(
        self,
        exchange: ExchangeType
    ) -> List[SymbolInfo]:
        symbols = await SymbolInfo.find({"exchange": exchange}).to_list()
        logger.debug(
            "Retrieved symbols for exchange",
            extra={"exchange": exchange, "count": len(symbols)}
        )
        return symbols

    @handle_db_error("Failed to retrieve all symbols", lambda self: {})
    async def get_all_symbols(self) -> Dict[ExchangeType, Dict[str, str]]:
        all_symbols = await SymbolInfo.find_all().to_list()
        result: Dict[ExchangeType, Dict[str, str]] = {}
        for info in all_symbols:
            result.setdefault(info.exchange, {})[info.symbol] = info.normalized_symbol
        logger.debug(
            "Retrieved all symbol mappings",
            extra={
                "exchange_count": len(result),
                "total_symbols": sum(len(symbols) for symbols in result.values())
            }
        )
        return result

    @handle_db_error("Failed to validate symbol", lambda self, symbol, exchange: {"symbol": symbol, "exchange": exchange})
    async def validate_symbol(
        self,
        symbol: str,
        exchange: ExchangeType
    ) -> bool:
        symbol_info = await self.get_symbol_info(symbol, exchange)
        if not symbol_info:
            return False
        specs = await SymbolSpecs.find_one({
            "symbol": symbol_info.normalized_symbol,
            "exchange": exchange
        })
        is_valid = specs is not None
        logger.debug(
            "Symbol validation result",
            extra={
                "symbol": symbol,
                "normalized": symbol_info.normalized_symbol,
                "exchange": exchange,
                "is_valid": is_valid
            }
        )
        return is_valid

    @handle_db_error("Failed to clean old symbol data", lambda self, max_age: {"max_age": str(max_age)})
    async def clean_old_data(self, max_age: timedelta) -> int:
        min_timestamp = datetime.utcnow() - max_age
        result = await SymbolInfo.delete_many({
            "last_updated": {"$lt": min_timestamp}
        })
        deleted_count = result.deleted_count
        logger.info(
            "Cleaned old symbol records",
            extra={"deleted_count": deleted_count, "max_age": str(max_age)}
        )
        return deleted_count

    @handle_db_error("Failed to bulk update symbols", lambda self, exchange, symbols_data: {"exchange": exchange, "symbols_data_count": len(symbols_data)})
    async def bulk_update(
        self,
        exchange: ExchangeType,
        symbols_data: List[Dict[str, str]]
    ) -> List[SymbolInfo]:
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
            extra={"exchange": exchange, "updated_count": len(updated_symbols)}
        )
        return updated_symbols

# Create singleton instance
symbol_info = CRUDSymbolInfo(SymbolInfo)
