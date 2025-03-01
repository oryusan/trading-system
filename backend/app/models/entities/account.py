"""
Account entity model with clear separation of concerns.

This model focuses on:
- Data structure definition
- Field validation
- Simple data transformations
- Pre-save validation hooks
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import ValidationError
from app.core.references import ExchangeType, ModelState
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class Account(Document):
    """
    Account model representing an exchange trading account.
    
    This model focuses purely on data structure and validation,
    with no direct service integration or complex business logic.
    """
    # Core fields
    user_id: Indexed(str) = Field(..., description="ID of the account owner")
    exchange: ExchangeType = Field(..., description="Exchange this account trades on")
    name: str = Field(..., description="Account display name")
    api_key: str = Field(..., description="Exchange API key")
    api_secret: str = Field(..., description="Exchange API secret")
    passphrase: Optional[str] = Field(None, description="Optional API passphrase")
    bot_id: Optional[str] = Field(None, description="Active bot reference")
    group_ids: List[str] = Field(default_factory=list, description="Associated group IDs")
    
    # Balance fields
    initial_balance: Decimal = Field(..., description="Initial balance")
    current_balance: Decimal = Field(..., description="Current available balance")
    current_equity: Decimal = Field(..., description="Current total equity")
    
    # Position tracking
    open_positions: int = Field(0, description="Current open positions")
    total_positions: int = Field(0, description="Total positions taken")
    successful_positions: int = Field(0, description="Profitable positions")
    position_value: Decimal = Field(Decimal("0"), description="Total position value")
    
    # Settings
    is_testnet: bool = Field(False, description="Using testnet")
    is_active: bool = Field(True, description="Account enabled")
    max_drawdown: float = Field(25.0, description="Max drawdown percentage")
    
    # Fees
    trading_fees: Decimal = Field(Decimal("0"), description="Accumulated trading fees")
    funding_fees: Decimal = Field(Decimal("0"), description="Accumulated funding fees")
    
    # Timestamps and error tracking
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    modified_at: Optional[datetime] = Field(None, description="Last modified timestamp")
    last_sync: Optional[datetime] = Field(None, description="Last balance sync")
    last_error: Optional[str] = Field(None, description="Last error message")
    error_count: int = Field(0, description="Consecutive errors")

    class Settings:
        """Collection settings and indexes."""
        name = "accounts"
        indexes = [
            "user_id",
            "exchange",
            "bot_id",
            "group_ids",
            "is_active",
            "created_at",
            [("exchange", 1), ("is_active", 1)],
            [("bot_id", 1), ("is_active", 1)]
        ]

    @field_validator("current_balance", "current_equity", "initial_balance")
    @classmethod
    def validate_balance(cls, value: Decimal) -> Decimal:
        """Ensure balance values are positive."""
        if value <= 0:
            raise ValidationError("Balance must be positive", context={"value": str(value)})
        return value

    @field_validator("api_key", "api_secret")
    @classmethod
    def validate_credentials_field(cls, value: str) -> str:
        """Validate API credential format."""
        credential = value.strip()
        if not credential:
            raise ValidationError("API credential cannot be empty", context={"credential": "*****"})
        if len(credential) < 16:
            raise ValidationError("API credential too short", context={"length": len(credential), "min_length": 16})
        if len(credential) > 128:
            raise ValidationError("API credential too long", context={"length": len(credential), "max_length": 128})
        if not all(c.isalnum() or c in "-_" for c in credential):
            raise ValidationError("Invalid characters in API credential", context={"credential": credential[:8] + "..."})
        return credential

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate model relationships before saving.
        
        This hook only checks reference existence and integrity without invoking 
        external services or implementing complex business logic.
        """
        try:
            from app.services.reference.manager import reference_manager
            
            # Validate user reference
            if not await reference_manager.validate_reference("Account", "User", self.user_id):
                raise ValidationError("Invalid user reference", context={"user_id": self.user_id})
            
            # Validate bot reference if specified
            if self.bot_id:
                if not await reference_manager.validate_reference("Account", "Bot", self.bot_id):
                    raise ValidationError("Invalid bot reference", context={"bot_id": self.bot_id})
            
            # Validate group references
            seen_groups = set()
            for group_id in self.group_ids:
                if group_id in seen_groups:
                    raise ValidationError("Duplicate group reference", context={"group_id": group_id})
                seen_groups.add(group_id)
                if not await reference_manager.validate_reference("Account", "Group", group_id):
                    raise ValidationError("Invalid group reference", context={"group_id": group_id})
            
            # Update modified timestamp
            self.touch()
            
        except ValidationError:
            raise
        except Exception as e:
            # Log reference validation failure but let the caller handle specific actions
            logger.error(
                "Reference validation failed", 
                extra={"account_id": str(self.id), "error": str(e)}
            )
            raise ValidationError("Reference validation failed", context={"error": str(e)})

    def to_dict(self) -> ModelState:
        """
        Convert account to a dictionary representation.
        
        This is a pure data transformation method with no external dependencies.
        """
        return {
            "account_info": {
                "id": str(self.id),
                "user_id": self.user_id,
                "name": self.name,
                "exchange": self.exchange.value,
                "is_testnet": self.is_testnet,
                "is_active": self.is_active,
            },
            "relationships": {
                "bot_id": self.bot_id,
                "group_ids": self.group_ids,
            },
            "balances": {
                "initial": str(self.initial_balance),
                "current": str(self.current_balance),
                "equity": str(self.current_equity),
            },
            "positions": {
                "open": self.open_positions,
                "total": self.total_positions,
                "successful": self.successful_positions,
                "value": str(self.position_value),
            },
            "fees": {
                "trading": str(self.trading_fees),
                "funding": str(self.funding_fees),
            },
            "settings": {
                "max_drawdown": self.max_drawdown,
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error,
            },
        }

    def __repr__(self) -> str:
        """String representation of the account."""
        return (
            f"Account(exchange={self.exchange}, balance={float(self.current_balance):.2f}, "
            f"positions={self.open_positions})"
        )

    def touch(self) -> None:
        """Update the modified timestamp."""
        self.modified_at = datetime.utcnow()