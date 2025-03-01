"""
Daily performance tracking model with enhanced service integration.

Features:
- Performance tracking with validation and derived metrics calculation
- Service integration for metric calculation and aggregation
- Helper methods for common tasks (e.g. date validation)
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.logging.logger import get_logger
from app.core.references import (
    # Exchange types
    ExchangeType,
    
    # User types
    UserRole,
    
    # Trading types
    BotStatus, 
    TimeFrame,
    OrderType,
    TradeSource,
    TradeStatus,
    
    # Position types
    PositionSide,
    PositionStatus,
    
    # For serialization
    ModelState
)

logger = get_logger(__name__)

class DailyPerformance(Document):
    """
    Daily trading performance with enhanced service integration.
    Trading metrics here are based solely on finalized (closed) trades.
    """
    account_id: Indexed(str) = Field(..., description="Account this performance belongs to")
    date: Indexed(str) = Field(..., description="Date in YYYY-MM-DD format")
    initial_balance: Decimal = Field(..., description="Initial balance when account was created")
    initial_equity: Decimal = Field(..., description="Initial equity when account was created")
    starting_balance: Decimal = Field(..., description="Balance at start of day")
    closing_balance: Decimal = Field(..., description="Balance at end of day")
    starting_equity: Decimal = Field(..., description="Equity at start of day")
    closing_equity: Decimal = Field(..., description="Equity at end of day")
    closed_trades: int = Field(0, description="Number of finalized (closed) trades for the day")
    winning_trades: int = Field(0, description="Number of profitable closed trades")
    closed_trade_value: Decimal = Field(0, description="Total notional value of closed trades for the day")
    daily_pnl: Decimal = Field(0, description="Gross profit/loss (PnL) for the day from closed trades")
    trading_fees: Decimal = Field(0, description="Trading fees (negative = paid)")
    funding_fees: Decimal = Field(0, description="Funding fees (negative = paid)")
    win_rate: float = Field(0, description="Percentage of winning positions", ge=0, le=100)
    roi_balance: float = Field(0, description="ROI based on initial balance")
    roi_equity: float = Field(0, description="ROI based on initial equity")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    modified_at: Optional[datetime] = Field(None, description="Last modification timestamp")
    last_error: Optional[str] = Field(None, description="Last error message")
    error_count: int = Field(0, description="Consecutive errors")

    class Settings:
        name = "daily_performance"
        indexes = [
            [("account_id", 1), ("date", 1)],
            "date",
            "created_at"
        ]

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError as e:
            raise ValueError(f"Invalid date format for {v}: expected 'YYYY-MM-DD'") from e

    @field_validator("closed_trades", "winning_trades")
    @classmethod
    def validate_trade_counts(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"Trade counts must be non-negative, got {v}")
        return v

    @field_validator("win_rate")
    @classmethod
    def validate_win_rate(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError(f"Win rate must be between 0 and 100, got {v}")
        return v

    @staticmethod
    def parse_date(date_str: str) -> datetime:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"Invalid date format for {date_str}: expected 'YYYY-MM-DD'") from e

    # --------------------
    # NEW METHOD: get_account_performance
    # --------------------
    @classmethod
    async def get_account_performance(cls, account_id: str, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Retrieve daily performance records for the given account within the specified date range.

        Args:
            account_id (str): The account identifier.
            start_date (datetime): Start date.
            end_date (datetime): End date.
        
        Returns:
            List[Dict[str, Any]]: List of performance records (as dictionaries).
        
        Raises:
            DatabaseError: If the query fails.
        """
        try:
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
            records = await cls.find({
                "account_id": account_id,
                "date": {"$gte": start_str, "$lte": end_str}
            }).sort("date").to_list()
            return [record.dict() for record in records]
        except Exception as e:
            raise DatabaseError(
                "Failed to get account performance",
                context={"account_id": account_id, "start_date": start_str, "end_date": end_str, "error": str(e)}
            )
    # --------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "balances": {
                "initial_balance": str(self.initial_balance),
                "initial_equity": str(self.initial_equity),
                "starting_balance": str(self.starting_balance),
                "closing_balance": str(self.closing_balance),
                "starting_equity": str(self.starting_equity),
                "closing_equity": str(self.closing_equity)
            },
            "trading": {
                "trades": self.closed_trades,
                "winning_trades": self.winning_trades,
                "volume": str(self.closed_trade_value),
                "daily_pnl": str(self.daily_pnl),
                "trading_fees": str(self.trading_fees),
                "funding_fees": str(self.funding_fees)
            },
            "metrics": {
                "win_rate": self.win_rate,
                "roi_balance": self.roi_balance,
                "roi_equity": self.roi_equity
            },
            "metadata": {
                "account_id": self.account_id,
                "date": self.date,
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error
            },
        }

    def __repr__(self) -> str:
        return (
            f"DailyPerformance(account={self.account_id}, date={self.date}, pnl={self.daily_pnl})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors.base import ValidationError, DatabaseError, ExchangeError
from app.core.logging.logger import get_logger

logger = get_logger(__name__)
