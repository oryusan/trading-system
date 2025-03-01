"""
Symbol data CRUD operations with centralized service integration.

This module serves as the primary integration point for all symbol-related services:
- Database operations for symbols (create, read, update, delete)
- Exchange API symbol validation
- Symbol normalization and standardization
- WebSocket symbol subscription management
- Symbol specifications (tick size, lot size, etc.)
"""

from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from decimal import Decimal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.symbol_data import SymbolData
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, ExchangeError
from app.core.references import ExchangeType
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

# Import services for centralized integration
from app.services.exchange.factory import exchange_factory, symbol_validator
from app.services.reference.manager import reference_manager
from app.services.cache.service import cache_service

logger = get_logger(__name__)


class SymbolDataCreate(BaseModel):
    """Schema for creating symbol data."""
    exchange: ExchangeType
    original_symbol: str = Field(..., description="Original symbol as provided by user/webhook")
    symbol: str = Field(..., description="Normalized exchange-specific trading symbol")
    tick_size: Decimal = Field(..., description="Price increment")
    lot_size: Decimal = Field(..., description="Quantity increment")
    contract_size: Decimal = Field(1, description="Contract multiplier for derivatives")
    base_currency: Optional[str] = None
    quote_currency: Optional[str] = None
    is_active: bool = True
    
    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase without spaces."""
        return v.strip().upper()
    
    @field_validator("original_symbol")
    @classmethod
    def validate_original_symbol(cls, v: str) -> str:
        """Store original symbol as provided."""
        if not v or not v.strip():
            raise ValidationError("Original symbol cannot be empty", context={"original_symbol": v})
        return v.strip()
    
    @field_validator("tick_size", "lot_size", "contract_size")
    @classmethod
    def validate_positive_decimal(cls, v: Decimal) -> Decimal:
        """Ensure decimal values are positive."""
        if v <= 0:
            raise ValidationError("Value must be positive", context={"value": str(v)})
        return v


class SymbolDataUpdate(BaseModel):
    """Schema for updating symbol data."""
    exchange: Optional[ExchangeType] = None
    symbol: Optional[str] = None
    tick_size: Optional[Decimal] = None
    lot_size: Optional[Decimal] = None
    contract_size: Optional[Decimal] = None
    base_currency: Optional[str] = None
    quote_currency: Optional[str] = None
    last_verified: Optional[datetime] = None
    is_active: Optional[bool] = None


class CRUDSymbol(CRUDBase[SymbolData, SymbolDataCreate, SymbolDataUpdate]):
    """
    CRUD operations for the SymbolData model with centralized service integration.
    
    This class handles all symbol-related operations and integrates with
    exchange services, caching, and reference validation.
    """
    
    @handle_db_error("Failed to get symbol by name and exchange", lambda self, symbol, exchange: {"symbol": symbol, "exchange": exchange})
    async def get_by_symbol_exchange(
        self, 
        symbol: str, 
        exchange: ExchangeType
    ) -> SymbolData:
        """Get a symbol by its name and exchange."""
        normalized_symbol = symbol.upper().strip()
        symbol_data = await SymbolData.find_one({
            "symbol": normalized_symbol,
            "exchange": exchange
        })
        
        if not symbol_data:
            raise NotFoundError(
                "Symbol not found", 
                context={"symbol": normalized_symbol, "exchange": exchange}
            )
        
        return symbol_data
    
    @handle_db_error("Failed to create symbol", lambda self, obj_in: {"symbol": obj_in.symbol, "exchange": obj_in.exchange})
    async def create(self, obj_in: SymbolDataCreate) -> SymbolData:
        """
        Create a new symbol with validation.
        
        Args:
            obj_in: Symbol creation data
            
        Returns:
            Created SymbolData instance
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Check if symbol already exists
        try:
            existing = await self.get_by_symbol_exchange(obj_in.symbol, obj_in.exchange)
            if existing:
                raise ValidationError(
                    "Symbol already exists",
                    context={"symbol": obj_in.symbol, "exchange": obj_in.exchange}
                )
        except NotFoundError:
            # Symbol doesn't exist, which is what we want when creating a new one
            pass
            
        # Create new symbol
        symbol_data = SymbolData(
            symbol=obj_in.symbol,
            exchange=obj_in.exchange,
            base_currency=obj_in.base_currency,
            quote_currency=obj_in.quote_currency,
            tick_size=obj_in.tick_size,
            lot_size=obj_in.lot_size,
            contract_size=obj_in.contract_size,
            is_active=obj_in.is_active,
            created_at=datetime.utcnow(),
            last_verified=datetime.utcnow()
        )
        
        # Save to database
        await symbol_data.save()
        
        # Cache the symbol data
        await cache_service.set(
            f"symbol:{obj_in.exchange}:{obj_in.symbol}",
            symbol_data.to_dict(),
            ttl=3600  # 1 hour cache
        )
        
        logger.info(
            "Created new symbol",
            extra={
                "symbol": obj_in.symbol,
                "exchange": obj_in.exchange,
                "id": str(symbol_data.id)
            }
        )
        
        return symbol_data
    
    @handle_db_error("Failed to verify symbol with exchange", lambda self, symbol, exchange: {"symbol": symbol, "exchange": exchange})
    async def verify_with_exchange(
        self,
        symbol: str,
        exchange: ExchangeType
    ) -> Dict[str, Any]:
        """
        Verify symbol specifications with the exchange.
        
        This ensures we have the latest tick size, lot size, etc.
        
        Args:
            symbol: Symbol to verify
            exchange: Exchange to verify against
            
        Returns:
            Dictionary with verification results
            
        Raises:
            ExchangeError: If verification fails
        """
        # Get current symbol data
        try:
            symbol_data = await self.get_by_symbol_exchange(symbol, exchange)
        except NotFoundError:
            # Symbol doesn't exist in our database yet
            symbol_data = None
        
        # Verify with exchange using symbol_validator
        validation_result = await symbol_validator.validate_symbol(
            symbol=symbol,
            exchange_type=exchange,
            force_validation=True
        )
        
        # Extract specifications
        specs = validation_result.get("specifications", {})
        
        # If symbol doesn't exist in our database, create it
        if not symbol_data:
            symbol_data = await self.create(SymbolDataCreate(
                symbol=symbol,
                exchange=exchange,
                base_currency=symbol.split('/')[0] if '/' in symbol else symbol[:-4],
                quote_currency=symbol.split('/')[1] if '/' in symbol else symbol[-4:],
                tick_size=Decimal(specs.get("tick_size", "0.1")),
                lot_size=Decimal(specs.get("lot_size", "0.001")),
                contract_size=Decimal(specs.get("contract_size", "1"))
            ))
        else:
            # Update existing symbol with latest specs
            updates = {}
            
            if "tick_size" in specs and Decimal(specs["tick_size"]) != symbol_data.tick_size:
                updates["tick_size"] = Decimal(specs["tick_size"])
                
            if "lot_size" in specs and Decimal(specs["lot_size"]) != symbol_data.lot_size:
                updates["lot_size"] = Decimal(specs["lot_size"])
                
            if "contract_size" in specs and Decimal(specs["contract_size"]) != symbol_data.contract_size:
                updates["contract_size"] = Decimal(specs["contract_size"])
            
            # Only update if there are changes
            if updates:
                updates["last_verified"] = datetime.utcnow()
                symbol_data = await self.update(symbol_data.id, updates)
        
        # Cache the result
        await cache_service.set(
            f"symbol:{exchange}:{symbol}",
            symbol_data.to_dict(),
            ttl=3600  # 1 hour cache
        )
        
        logger.info(
            "Verified symbol with exchange",
            extra={
                "symbol": symbol,
                "exchange": exchange,
                "tick_size": str(symbol_data.tick_size),
                "lot_size": str(symbol_data.lot_size)
            }
        )
        
        return {
            "symbol": symbol_data.symbol,
            "exchange": symbol_data.exchange,
            "tick_size": str(symbol_data.tick_size),
            "lot_size": str(symbol_data.lot_size),
            "contract_size": str(symbol_data.contract_size),
            "last_verified": symbol_data.last_verified.isoformat() if symbol_data.last_verified else None
        }
    
    @handle_db_error("Failed to get symbol specifications", lambda self, symbol, exchange: {"symbol": symbol, "exchange": exchange})
    async def get_specifications(
        self,
        symbol: str,
        exchange: ExchangeType
    ) -> Dict[str, Any]:
        """
        Get symbol specifications with optional exchange verification.
        
        This method tries to retrieve from cache first, then database,
        and finally falls back to exchange verification if needed.
        
        Args:
            symbol: Symbol to get specifications for
            exchange: Exchange to get specifications from
            
        Returns:
            Dictionary with symbol specifications
        """
        # Normalize symbol
        normalized_symbol = symbol.upper().strip()
        
        # Try to get from cache first
        cache_key = f"symbol:{exchange}:{normalized_symbol}"
        cached_data = await cache_service.get(cache_key)
        
        if cached_data:
            logger.debug(
                "Retrieved symbol specifications from cache",
                extra={"symbol": normalized_symbol, "exchange": exchange}
            )
            return cached_data
        
        # Try to get from database
        try:
            symbol_data = await self.get_by_symbol_exchange(normalized_symbol, exchange)
            
            # Check if verification is needed (older than 24 hours)
            needs_verification = (
                not symbol_data.last_verified or 
                (datetime.utcnow() - symbol_data.last_verified) > timedelta(hours=24)
            )
            
            if needs_verification:
                # Verify with exchange in background task
                # We still return the database data immediately
                asyncio.create_task(
                    self.verify_with_exchange(normalized_symbol, exchange)
                )
            
            # Cache the result
            await cache_service.set(
                cache_key,
                symbol_data.to_dict(),
                ttl=3600  # 1 hour cache
            )
            
            return symbol_data.to_dict()
            
        except NotFoundError:
            # Symbol not found in database, verify with exchange
            verification_result = await self.verify_with_exchange(normalized_symbol, exchange)
            return verification_result
    
    @handle_db_error("Failed to get active symbols", lambda self, exchange: {"exchange": exchange})
    async def get_active_symbols(
        self,
        exchange: Optional[ExchangeType] = None
    ) -> List[SymbolData]:
        """
        Get all active trading symbols, optionally filtered by exchange.
        
        Args:
            exchange: Optional exchange filter
            
        Returns:
            List of active SymbolData instances
        """
        query = {"is_active": True}
        if exchange:
            query["exchange"] = exchange
        
        symbols = await SymbolData.find(query).to_list()
        
        logger.info(
            "Retrieved active symbols",
            extra={"exchange": exchange, "count": len(symbols)}
        )
        
        return symbols
    
    @handle_db_error("Failed to normalize symbol", lambda self, symbol, exchange: {"symbol": symbol, "exchange": exchange})
    async def normalize_symbol(
        self,
        symbol: str,
        exchange: ExchangeType
    ) -> str:
        """
        Normalize a symbol for a specific exchange.
        
        Different exchanges use different formats for the same symbol.
        This method standardizes the symbol format.
        
        Args:
            symbol: Symbol to normalize
            exchange: Exchange context
            
        Returns:
            Normalized symbol string
        """
        # Try to use symbol_validator for normalization
        try:
            validation_result = await symbol_validator.validate_symbol(symbol, exchange)
            return validation_result.get("normalized", symbol.upper())
        except Exception as e:
            logger.warning(
                f"Symbol validation failed, using simple normalization",
                extra={"symbol": symbol, "exchange": exchange, "error": str(e)}
            )
            # Simple normalization fallback
            return symbol.upper().replace("-", "").replace("/", "")
    
    @handle_db_error("Failed to bulk update symbols", lambda self, symbols_data: {"count": len(symbols_data)})
    async def bulk_update_from_exchange(
        self,
        exchange: ExchangeType
    ) -> Dict[str, Any]:
        """
        Bulk update symbols from exchange.
        
        This method fetches all available symbols from an exchange
        and updates or creates them in our database.
        
        Args:
            exchange: Exchange to update symbols for
            
        Returns:
            Dictionary with update results
        """
        # Get exchange operations instance
        exchange_instance = await exchange_factory.get_exchange_class(exchange)(None)
        
        # Get all symbols from exchange
        symbols = await exchange_instance.get_all_symbols()
        
        # Track results
        created = 0
        updated = 0
        failed = 0
        
        # Process each symbol
        for symbol_info in symbols:
            try:
                symbol = symbol_info["symbol"]
                
                # Try to get existing symbol
                try:
                    symbol_data = await self.get_by_symbol_exchange(symbol, exchange)
                    
                    # Update symbol with new information
                    updates = {}
                    
                    if "tick_size" in symbol_info and Decimal(str(symbol_info["tick_size"])) != symbol_data.tick_size:
                        updates["tick_size"] = Decimal(str(symbol_info["tick_size"]))
                        
                    if "lot_size" in symbol_info and Decimal(str(symbol_info["lot_size"])) != symbol_data.lot_size:
                        updates["lot_size"] = Decimal(str(symbol_info["lot_size"]))
                        
                    if "contract_size" in symbol_info and Decimal(str(symbol_info["contract_size"])) != symbol_data.contract_size:
                        updates["contract_size"] = Decimal(str(symbol_info["contract_size"]))
                    
                    # Update only if needed
                    if updates:
                        updates["last_verified"] = datetime.utcnow()
                        await self.update(symbol_data.id, updates)
                        updated += 1
                        
                except NotFoundError:
                    # Symbol doesn't exist, create it
                    base_currency = symbol_info.get("base_currency", symbol.split('/')[0] if '/' in symbol else symbol[:-4])
                    quote_currency = symbol_info.get("quote_currency", symbol.split('/')[1] if '/' in symbol else symbol[-4:])
                    
                    await self.create(SymbolDataCreate(
                        symbol=symbol,
                        exchange=exchange,
                        base_currency=base_currency,
                        quote_currency=quote_currency,
                        tick_size=Decimal(str(symbol_info.get("tick_size", "0.1"))),
                        lot_size=Decimal(str(symbol_info.get("lot_size", "0.001"))),
                        contract_size=Decimal(str(symbol_info.get("contract_size", "1")))
                    ))
                    created += 1
                    
            except Exception as e:
                logger.error(
                    f"Failed to process symbol {symbol_info.get('symbol', 'unknown')}",
                    extra={"error": str(e), "exchange": exchange}
                )
                failed += 1
        
        logger.info(
            "Bulk updated symbols from exchange",
            extra={
                "exchange": exchange,
                "created": created,
                "updated": updated,
                "failed": failed
            }
        )
        
        return {
            "exchange": exchange,
            "created": created,
            "updated": updated,
            "failed": failed,
            "total": created + updated + failed
        }
    
    @handle_db_error("Failed to disable inactive symbols", lambda self, days_threshold: {"days_threshold": days_threshold})
    async def disable_inactive_symbols(
        self,
        days_threshold: int = 30
    ) -> Dict[str, int]:
        """
        Disable symbols that haven't been verified in the specified time period.
        
        Args:
            days_threshold: Number of days of inactivity before disabling
            
        Returns:
            Dictionary with results
        """
        threshold_date = datetime.utcnow() - timedelta(days=days_threshold)
        
        query = {
            "is_active": True,
            "last_verified": {"$lt": threshold_date}
        }
        
        # Find symbols to disable
        symbols_to_disable = await SymbolData.find(query).to_list()
        
        # Disable each symbol
        disabled_count = 0
        for symbol in symbols_to_disable:
            try:
                symbol.is_active = False
                symbol.modified_at = datetime.utcnow()
                await symbol.save()
                
                # Invalidate cache
                cache_key = f"symbol:{symbol.exchange}:{symbol.symbol}"
                await cache_service.delete(cache_key)
                
                disabled_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to disable symbol {symbol.symbol}",
                    extra={"symbol_id": str(symbol.id), "error": str(e)}
                )
        
        logger.info(
            "Disabled inactive symbols",
            extra={"disabled_count": disabled_count, "threshold_days": days_threshold}
        )
        
        return {"disabled_count": disabled_count, "found_count": len(symbols_to_disable)}

    @handle_db_error("Failed to bulk validate symbols", 
                    lambda self, symbols, exchange: {"exchange": exchange.value if hasattr(exchange, "value") else exchange, "symbol_count": len(symbols)})
    async def bulk_validate(
        self, 
        symbols: List[str], 
        exchange: ExchangeType
    ) -> Dict[str, Dict[str, Any]]:
        """
        Validate multiple symbols in a single operation.
        
        Args:
            symbols: List of symbols to validate
            exchange: Exchange type
            
        Returns:
            Dictionary mapping symbols to validation results
        """
        # Process each symbol concurrently
        tasks = [self.verify_with_exchange(symbol, exchange) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        validation_map = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                validation_map[symbol] = {
                    "is_valid": False,
                    "error": str(result),
                    "original": symbol
                }
            else:
                validation_map[symbol] = {
                    "is_valid": True,
                    "symbol": result["symbol"],
                    "specifications": {
                        "tick_size": result["tick_size"],
                        "lot_size": result["lot_size"],
                        "contract_size": result["contract_size"],
                    }
                }
        
        return validation_map


# Import asyncio for background tasks
import asyncio

# Create singleton instance
symbol = CRUDSymbol(SymbolData)