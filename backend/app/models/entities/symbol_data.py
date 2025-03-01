"""
Symbol data entity model with clear distinction between original and normalized symbols.

This model represents trading symbol specifications including:
- Original user-provided symbol and exchange-normalized symbol
- Trading specifications (tick size, lot size, contract size)
- Currency information
- Activity status and verification timestamps

The model focuses solely on data structure and validation,
with no direct service integration or complex business logic.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import ValidationError
from app.core.references import ExchangeType, ModelState
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class SymbolData(Document):
    """
    Symbol data model representing trading symbol specifications.
    
    This model focuses on:
    - Data structure and validation
    - Proper field typing
    - Pre-save hooks for internal validation
    
    Service integration is handled in the CRUD layer.
    """

    # Core fields
    original_symbol: str = Field(
        ...,
        description="Original symbol as provided by user/webhook"
    )
    symbol: Indexed(str) = Field(
        ...,
        description="Normalized exchange-specific trading symbol (uppercase)"
    )
    exchange: Indexed(ExchangeType) = Field(
        ...,
        description="Exchange this symbol trades on"
    )
    
    # Trading specifications
    tick_size: Decimal = Field(
        ...,
        description="Price increment (minimum price change)"
    )
    lot_size: Decimal = Field(
        ...,
        description="Quantity increment (minimum quantity change)"
    )
    contract_size: Decimal = Field(
        Decimal("1"),
        description="Contract multiplier for derivatives"
    )
    
    # Currency information
    base_currency: Optional[str] = Field(
        None,
        description="Base currency/asset"
    )
    quote_currency: Optional[str] = Field(
        None,
        description="Quote currency/asset"
    )
    
    # Status and verification
    is_active: bool = Field(
        True,
        description="Whether symbol is active for trading"
    )
    last_verified: Optional[datetime] = Field(
        None,
        description="Last verification timestamp"
    )
    
    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification timestamp"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "symbol_data"
        indexes = [
            "symbol",
            "exchange",
            "original_symbol",
            [("symbol", 1), ("exchange", 1)],
            [("original_symbol", 1), ("exchange", 1)],
            "is_active"
        ]

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Validate and normalize exchange symbol."""
        if not v or not v.strip():
            raise ValidationError("Symbol cannot be empty", context={"symbol": v})
        return v.upper().strip()
    
    @field_validator("original_symbol")
    @classmethod
    def validate_original_symbol(cls, v: str) -> str:
        """Validate original symbol."""
        if not v or not v.strip():
            raise ValidationError("Original symbol cannot be empty", context={"original_symbol": v})
        return v.strip()
    
    @field_validator("tick_size", "lot_size", "contract_size")
    @classmethod
    def validate_positive_decimal(cls, v: Decimal) -> Decimal:
        """Ensure values are positive."""
        if v <= 0:
            raise ValidationError("Value must be positive", context={"value": str(v)})
        return v

    @before_event([Replace, Insert])
    async def pre_save_hooks(self):
        """Perform validation before saving the document."""
        # Update modified timestamp
        self.modified_at = datetime.utcnow()
        
        # Ensure symbol is normalized
        self.symbol = self.symbol.upper().strip()

    def to_dict(self) -> ModelState:
        """
        Convert to a dictionary representation for API responses.
        
        This is a pure data transformation method with no external dependencies.
        """
        return {
            "id": str(self.id),
            "original_symbol": self.original_symbol,
            "symbol": self.symbol,
            "exchange": self.exchange.value if hasattr(self.exchange, "value") else self.exchange,
            "specifications": {
                "tick_size": str(self.tick_size),
                "lot_size": str(self.lot_size),
                "contract_size": str(self.contract_size)
            },
            "currency": {
                "base": self.base_currency,
                "quote": self.quote_currency
            },
            "status": {
                "is_active": self.is_active,
                "last_verified": self.last_verified.isoformat() if self.last_verified else None
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None
            }
        }

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"SymbolData(original='{self.original_symbol}', symbol='{self.symbol}', exchange={self.exchange}, active={self.is_active})"