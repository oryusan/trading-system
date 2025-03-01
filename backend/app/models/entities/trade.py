"""
Trade entity model focused purely on data structure, validation, and core state transitions.

This model has no direct service integration and focuses solely on:
- Core data structure
- Field validation that doesn't require external services
- State transitions and calculations
- Serialization helpers
"""

from datetime import datetime
from typing import Optional, Dict, List, Any, Union
from decimal import Decimal

from beanie import Document, Indexed
from pydantic import Field, field_validator, FieldValidationInfo

from app.core.errors.base import ValidationError
from app.core.references import TradeStatus, OrderType, TradeSource, PositionSide, ModelState
from app.core.config.constants import trading_constants
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class Trade(Document):
    """
    Trade entity model for persistence and state management.
    
    This model focuses on:
    - Data structure and validation
    - State transitions and lifecycle
    - P&L calculations
    - Serialization
    
    Database operations and service integration are handled in the CRUD layer.
    """

    # Core fields
    account_id: Indexed(str) = Field(
        ...,
        description="Account executing trade"
    )
    bot_id: Optional[str] = Field(
        None,
        description="Bot initiating trade"
    )
    symbol: str = Field(
        ...,
        description="Trading symbol"
    )
    order_type: OrderType = Field(
        ...,
        description="Type of trade order"
    )
    side: PositionSide = Field(
        ...,
        description="Trading side (buy/sell)"
    )
    size: Decimal = Field(
        ...,
        description="Trade size in units (quantity of the asset)"
    )

    # Risk parameters
    leverage: int = Field(
        trading_constants.MIN_LEVERAGE,
        description="Position leverage",
        ge=trading_constants.MIN_LEVERAGE,
        le=trading_constants.MAX_LEVERAGE
    )
    risk_percentage: Decimal = Field(
        ...,
        description="Risk percentage relative to balance",
        ge=Decimal(str(trading_constants.MIN_RISK_PERCENTAGE)),
        le=Decimal(str(trading_constants.MAX_RISK_PERCENTAGE))
    )
    order_size: Decimal = Field(
        ...,
        description="Order size in USD value (size * entry_price)",
        gt=0
    )

    # Price fields
    entry_price: Optional[Decimal] = Field(
        None,
        description="Execution entry price"
    )
    take_profit: Optional[Decimal] = Field(
        None,
        description="Take profit price"
    )
    stop_loss: Optional[Decimal] = Field(
        None,
        description="Stop loss price"
    )
    exit_price: Optional[Decimal] = Field(
        None,
        description="Position exit price"
    )

    # Performance tracking
    trading_fees: Decimal = Field(
        Decimal("0"),
        description="Trading fees incurred"
    )
    funding_fees: Decimal = Field(
        Decimal("0"),
        description="Funding fees paid"
    )
    pnl: Optional[Decimal] = Field(
        None,
        description="Raw profit/loss"
    )
    pnl_percentage: Optional[float] = Field(
        None,
        description="P/L percentage relative to order_size"
    )

    # Status tracking
    status: TradeStatus = Field(
        TradeStatus.PENDING,
        description="Current trade status"
    )
    source: TradeSource = Field(
        ...,
        description="Trade signal source"
    )

    # Timestamps
    executed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Initial execution time"
    )
    closed_at: Optional[datetime] = Field(
        None,
        description="Position close time"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification time"
    )

    # Exchange tracking  
    exchange_order_id: Optional[str] = Field(
        None,
        description="Exchange order ID"
    )
    exchange_status: Optional[str] = Field(
        None,
        description="Exchange order status"
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "trades"
        indexes = [
            "account_id",
            "bot_id",
            "symbol",
            "status",
            "executed_at",
            [("account_id", 1), ("status", 1)],
            [("bot_id", 1), ("executed_at", -1)],
            [("exchange_order_id", 1)]
        ]

    @field_validator("leverage", "risk_percentage")
    @classmethod
    def validate_risk_params(cls, v, info: FieldValidationInfo) -> Any:
        """
        Validate risk parameters are within allowed ranges.

        For 'leverage', ensure it is between the constants MIN_LEVERAGE and MAX_LEVERAGE.
        For 'risk_percentage', ensure it is between MIN_RISK_PERCENTAGE and MAX_RISK_PERCENTAGE.
        """
        if info.field_name == "leverage":
            if not (trading_constants.MIN_LEVERAGE <= v <= trading_constants.MAX_LEVERAGE):
                raise ValidationError(
                    f"Leverage must be between {trading_constants.MIN_LEVERAGE} and {trading_constants.MAX_LEVERAGE}",
                    context={"leverage": v}
                )
        elif info.field_name == "risk_percentage":
            min_risk = Decimal(str(trading_constants.MIN_RISK_PERCENTAGE))
            max_risk = Decimal(str(trading_constants.MAX_RISK_PERCENTAGE))
            if not (min_risk <= v <= max_risk):
                raise ValidationError(
                    f"Risk percentage must be between {trading_constants.MIN_RISK_PERCENTAGE} and {trading_constants.MAX_RISK_PERCENTAGE}",
                    context={"risk_percentage": str(v)}
                )
        return v

    def _validate_close_conditions(self) -> None:
        """
        Ensure that the trade is in a state that allows it to be closed.
        
        Raises:
            ValidationError: If the trade cannot be closed in its current state
        """
        if self.status != TradeStatus.OPEN:
            raise ValidationError(
                "Trade cannot be closed in current status",
                context={
                    "trade_id": str(self.id),
                    "current_status": self.status.value,
                    "required_status": TradeStatus.OPEN.value
                }
            )
        if self.entry_price is None:
            raise ValidationError(
                "Trade missing entry price",
                context={"trade_id": str(self.id)}
            )

    def _update_trade_state(
        self,
        exit_price: Decimal,
        trading_fees: Optional[Decimal],
        funding_fees: Optional[Decimal]
    ) -> None:
        """
        Update the trade's state to closed and record fees and exit price.
        
        Args:
            exit_price: Position exit price
            trading_fees: Optional trading fees 
            funding_fees: Optional funding fees
        """
        self.exit_price = exit_price
        self.trading_fees = trading_fees if trading_fees is not None else self.trading_fees
        self.funding_fees = funding_fees if funding_fees is not None else self.funding_fees
        self.status = TradeStatus.CLOSED
        self.closed_at = datetime.utcnow()

    def _calculate_pnl(self) -> None:
        """
        Calculate profit/loss (P/L) and P/L percentage.

        Raises:
            ValidationError: If the calculation fails.
        """
        try:
            price_diff = self.exit_price - self.entry_price
            multiplier = Decimal("1") if self.side == PositionSide.BUY else Decimal("-1")
            self.pnl = price_diff * self.size * multiplier

            total_fees = self.trading_fees + self.funding_fees
            net_pnl = self.pnl - total_fees

            cost_basis = self.entry_price * self.size
            if cost_basis > 0:
                self.pnl_percentage = float(net_pnl / cost_basis * 100)
        except Exception as e:
            raise ValidationError(
                "P/L calculation failed",
                context={
                    "entry_price": str(self.entry_price),
                    "exit_price": str(self.exit_price),
                    "size": str(self.size),
                    "error": str(e)
                }
            )

    def get_trade_info(self) -> Dict[str, Any]:
        """
        Get comprehensive trade information formatted for API responses.
        
        Returns a structured dictionary with all trade details organized
        into logical sections for easy consumption by clients.
        
        Returns:
            Dict with formatted trade information
        """
        return {
            "trade_id": str(self.id),
            "account_id": self.account_id,
            "bot_id": self.bot_id,
            "details": {
                "symbol": self.symbol,
                "side": self.side.value,
                "order_type": self.order_type.value,
                "size": str(self.size),
                "order_size": str(self.order_size)
            },
            "risk": {
                "leverage": self.leverage,
                "risk_percentage": str(self.risk_percentage)
            },
            "execution": {
                "entry_price": str(self.entry_price) if self.entry_price is not None else None,
                "exit_price": str(self.exit_price) if self.exit_price is not None else None,
                "take_profit": str(self.take_profit) if self.take_profit is not None else None,
                "stop_loss": str(self.stop_loss) if self.stop_loss is not None else None
            },
            "performance": {
                "pnl": str(self.pnl) if self.pnl is not None else None,
                "pnl_percentage": self.pnl_percentage,
                "trading_fees": str(self.trading_fees),
                "funding_fees": str(self.funding_fees)
            },
            "status": {
                "status": self.status.value,
                "source": self.source.value,
                "exchange_status": self.exchange_status,
                "last_error": self.last_error
            },
            "timestamps": {
                "executed_at": self.executed_at.isoformat(),
                "closed_at": self.closed_at.isoformat() if self.closed_at else None,
                "modified_at": self.modified_at.isoformat() if self.modified_at else None
            }
        }

    def to_dict(self) -> ModelState:
        """
        Convert to a dictionary format suitable for serialization.
        
        This method provides a more compact representation than get_trade_info(),
        meant primarily for internal use and serialization.
        
        Returns:
            Dictionary with all trade data
        """
        return {
            "trade_info": {
                "id": str(self.id),
                "account_id": self.account_id,
                "bot_id": self.bot_id,
                "symbol": self.symbol,
                "status": self.status.value
            },
            "order_info": {
                "type": self.order_type.value,
                "side": self.side.value,
                "size": str(self.size),
                "order_size": str(self.order_size)
            },
            "risk_params": {
                "leverage": self.leverage,
                "risk_percentage": str(self.risk_percentage)
            },
            "prices": {
                "entry_price": str(self.entry_price) if self.entry_price is not None else None,
                "exit_price": str(self.exit_price) if self.exit_price is not None else None,
                "take_profit": str(self.take_profit) if self.take_profit is not None else None,
                "stop_loss": str(self.stop_loss) if self.stop_loss is not None else None
            },
            "performance": {
                "pnl": str(self.pnl) if self.pnl is not None else None,
                "pnl_percentage": self.pnl_percentage,
                "trading_fees": str(self.trading_fees),
                "funding_fees": str(self.funding_fees)
            },
            "tracking": {
                "status": self.status.value,
                "source": self.source.value,
                "exchange_status": self.exchange_status,
                "exchange_order_id": self.exchange_order_id,
                "last_error": self.last_error
            },
            "timestamps": {
                "executed_at": self.executed_at.isoformat(),
                "closed_at": self.closed_at.isoformat() if self.closed_at else None,
                "modified_at": self.modified_at.isoformat() if self.modified_at else None
            }
        }

    def __repr__(self) -> str:
        """String representation for logging and debugging."""
        return (
            f"Trade(id={str(self.id)}, symbol={self.symbol}, "
            f"side={self.side.value}, status={self.status.value})"
        )