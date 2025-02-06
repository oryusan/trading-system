"""
Trade model focused on data persistence, validation, and state tracking.

Features:
- Core trade data modeling
- Reference validation
- State tracking
- Performance metrics
"""

from datetime import datetime
from typing import Optional, Dict, List, Any
from decimal import Decimal
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors import (
    ValidationError,
    DatabaseError,
    NotFoundError
)
from app.core.references import (
    TradeStatus,
    OrderType,
    TradeSource,
    PositionSide,
    ModelState
)

class Trade(Document):
    """
    Trade model for persisting trade data and state.
    
    Features:
    - Trade data persistence
    - Reference validation
    - State tracking
    - Performance metrics
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
        description="Trade size in units"
    )
    
    # Risk parameters
    leverage: int = Field(
        1,
        description="Position leverage",
        ge=1,
        le=100
    )
    risk_percentage: Decimal = Field(
        ...,
        description="Risk percentage relative to balance",
        gt=0,
        le=100  
    )
    position_size: Decimal = Field(
        ...,
        description="Position size in USD",
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
        Decimal('0'),
        description="Trading fees incurred"
    )
    funding_fees: Decimal = Field(
        Decimal('0'),
        description="Funding fees paid"  
    )
    pnl: Optional[Decimal] = Field(
        None,
        description="Raw profit/loss"
    )
    pnl_percentage: Optional[float] = Field(
        None,
        description="P/L percentage relative to position size"
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
            [("bot_id", 1), ("executed_at", -1)]
        ]

    @field_validator("leverage", "risk_percentage")
    @classmethod
    def validate_risk_params(cls, v: float) -> float:
        """Validate risk parameters are within allowed ranges."""
        try:
            if isinstance(v, int):
                if not 1 <= v <= 100:
                    raise ValidationError(
                        "Leverage must be between 1 and 100",
                        context={"leverage": v}
                    )
            else:
                if not 0 < float(v) <= 100:
                    raise ValidationError(
                        "Risk percentage must be between 0 and 100",
                        context={"risk_percentage": str(v)}
                    )
            return v
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Invalid risk parameter",
                context={"value": v, "error": str(e)}
            )

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate trade references before saving.
        
        Validates:
        - Account reference exists
        - Bot reference exists if specified
        """
        try:
            # Validate account reference
            valid = await ref_manager.validate_reference(
                source_type="Trade",
                target_type="Account",
                reference_id=self.account_id
            )
            if not valid:
                raise NotFoundError(
                    "Referenced account not found",
                    context={"account_id": self.account_id}
                )

            # Validate bot reference if present  
            if self.bot_id:
                valid = await ref_manager.validate_reference(
                    source_type="Trade",
                    target_type="Bot",
                    reference_id=self.bot_id  
                )
                if not valid:
                    raise NotFoundError(
                        "Referenced bot not found",
                        context={"bot_id": self.bot_id}
                    )

            # Update modified timestamp
            self.modified_at = datetime.utcnow()

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "trade_id": str(self.id),
                    "account_id": self.account_id,
                    "error": str(e)
                }
            )

    async def close(
        self,
        exit_price: Decimal,
        trading_fees: Optional[Decimal] = None,
        funding_fees: Optional[Decimal] = None
    ) -> None:
        """
        Update trade state to closed with final metrics.
        
        Args:
            exit_price: Position exit price
            trading_fees: Optional trading fees
            funding_fees: Optional funding fees
            
        Raises:
            ValidationError: If position cannot be closed
            DatabaseError: If updates fail
        """
        try:
            # Validate trade can be closed
            if self.status != TradeStatus.OPEN:
                raise ValidationError(
                    "Trade cannot be closed in current status",
                    context={
                        "trade_id": str(self.id),
                        "current_status": self.status,
                        "required_status": TradeStatus.OPEN
                    }
                )

            if not self.entry_price:
                raise ValidationError(
                    "Trade missing entry price",
                    context={"trade_id": str(self.id)}
                )

            # Update trade state  
            self.exit_price = exit_price
            self.trading_fees = trading_fees or self.trading_fees 
            self.funding_fees = funding_fees or self.funding_fees
            self.status = TradeStatus.CLOSED
            self.closed_at = datetime.utcnow()

            # Calculate P/L
            try:
                # Raw P/L calculation
                price_diff = self.exit_price - self.entry_price
                multiplier = Decimal('1') if self.side == PositionSide.BUY else Decimal('-1')
                self.pnl = price_diff * self.size * multiplier

                # Add fees
                total_fees = self.trading_fees + self.funding_fees
                net_pnl = self.pnl - total_fees

                # Calculate percentage
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

            # Update performance tracking
            try:
                await performance_service.update_trade_result(
                    account_id=self.account_id,
                    metrics={
                        "pnl": float(self.pnl),
                        "trading_fees": float(self.trading_fees),
                        "funding_fees": float(self.funding_fees),
                        "position_size": float(self.position_size),
                        "is_successful": self.pnl > 0
                    }
                )
            except Exception as e:
                logger.error(
                    "Failed to update performance metrics",
                    extra={
                        "trade_id": str(self.id),
                        "error": str(e)
                    }
                )

            # Save updates
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Closed trade",
                extra={
                    "trade_id": str(self.id), 
                    "pnl": str(self.pnl),
                    "pnl_percentage": self.pnl_percentage
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to close trade",
                context={
                    "trade_id": str(self.id),
                    "exit_price": str(exit_price),
                    "error": str(e)
                }
            )

    async def update_exchange_status(
        self,
        status: str,
        error: Optional[str] = None
    ) -> None:
        """
        Update exchange-related status information.
        
        Args:
            status: New exchange status
            error: Optional error message
            
        Raises:
            DatabaseError: If update fails
        """
        try:
            self.exchange_status = status
            self.last_error = error
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Updated exchange status",
                extra={
                    "trade_id": str(self.id),
                    "status": status,
                    "error": error
                }
            )

        except Exception as e:
            raise DatabaseError(
                "Failed to update exchange status",
                context={
                    "trade_id": str(self.id),
                    "status": status, 
                    "error": str(e)
                }
            )

    @classmethod
    async def get_account_trades(
        cls,
        account_id: str,
        status: Optional[TradeStatus] = None,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List["Trade"]:
        """
        Get trades for an account with optional filtering.
        
        Args:
            account_id: Account to get trades for
            status: Optional status filter
            symbol: Optional symbol filter
            limit: Maximum trades to return
            
        Returns:
            List[Trade]: Matching trades
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If query fails
        """
        try:
            # Build query
            query = {"account_id": account_id}
            
            if status:
                query["status"] = status
                
            if symbol:
                query["symbol"] = symbol.upper()

            trades = await cls.find(query).sort("-executed_at").limit(limit).to_list()

            logger.info(
                "Retrieved account trades",
                extra={
                    "account_id": account_id,
                    "status": status,
                    "symbol": symbol,
                    "count": len(trades)
                }
            )

            return trades
            
        except Exception as e:
            raise DatabaseError(
                "Failed to get account trades",
                context={
                    "account_id": account_id,
                    "status": status.value if status else None,
                    "symbol": symbol,
                    "error": str(e)
                }
            )

    async def get_trade_info(self) -> Dict[str, Any]:
        """Get comprehensive trade information."""
        return {
            "trade_id": str(self.id),
            "account_id": self.account_id,
            "bot_id": self.bot_id,
            "details": {
                "symbol": self.symbol,
                "side": self.side.value,
                "order_type": self.order_type.value,
                "size": str(self.size),
                "position_size": str(self.position_size)
            },
            "risk": {
                "leverage": self.leverage,
                "risk_percentage": str(self.risk_percentage)
            },
            "execution": {
                "entry_price": str(self.entry_price) if self.entry_price else None,
                "exit_price": str(self.exit_price) if self.exit_price else None,
                "take_profit": str(self.take_profit) if self.take_profit else None,
                "stop_loss": str(self.stop_loss) if self.stop_loss else None
            },
            "performance": {
                "pnl": str(self.pnl) if self.pnl else None,
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
        """Convert to dictionary format."""
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
                "position_size": str(self.position_size)
            },
            "risk_params": {
                "leverage": self.leverage,
                "risk_percentage": str(self.risk_percentage)
            },
            "prices": {
                "entry_price": str(self.entry_price) if self.entry_price else None,
                "exit_price": str(self.exit_price) if self.exit_price else None,
                "take_profit": str(self.take_profit) if self.take_profit else None,
                "stop_loss": str(self.stop_loss) if self.stop_loss else None
            },
            "performance": {
                "pnl": str(self.pnl) if self.pnl else None,
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
        """String representation."""
        return (
            f"Trade(symbol={self.symbol}, "
            f"side={self.side.value}, "
            f"status={self.status.value})"
        )

# Import at end to avoid circular dependencies
from app.core.logging.logger import get_logger
from app.services.reference.manager import ref_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)