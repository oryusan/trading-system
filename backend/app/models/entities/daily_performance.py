"""
Daily performance tracking model with enhanced service integration.

Features:
- Performance tracking with validation
- Service integration
- Enhanced error handling with rich context
- Reference integrity validation
- Proper logging
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

class DailyPerformance(Document):
    """
    Daily trading performance with enhanced service integration.
    
    Features:
    - Balance tracking
    - Trade metrics
    - Fee accounting
    - Performance calculations
    - Error handling
    """
    
    # Core fields
    account_id: Indexed(str) = Field(
        ..., 
        description="Account this performance belongs to"
    )
    date: Indexed(str) = Field(
        ...,
        description="Date in YYYY-MM-DD format"
    )
    
    # Balance metrics
    initial_balance: Decimal = Field(
        ...,
        description="Initial balance when account was created"
    )
    initial_equity: Decimal = Field(
        ...,
        description="Initial equity when account was created"
    )
    starting_balance: Decimal = Field(
        ...,
        description="Balance at start of day"
    )
    closing_balance: Decimal = Field(
        ...,
        description="Balance at end of day"
    )
    starting_equity: Decimal = Field(
        ...,
        description="Equity at start of day"
    )
    closing_equity: Decimal = Field(
        ...,
        description="Equity at end of day"
    )
    
    # Trading metrics
    trades: int = Field(
        0,
        description="Number of positions closed"
    )
    winning_trades: int = Field(
        0,
        description="Number of profitable positions"
    )
    volume: Decimal = Field(
        0,
        description="Total trading volume for the day"
    )
    daily_pnl: Decimal = Field(
        0,
        description="Raw profit/loss for the day"
    )
    trading_fees: Decimal = Field(
        0,
        description="Trading fees (negative = paid)"
    )
    funding_fees: Decimal = Field(
        0,
        description="Funding fees (negative = paid)"
    )
    
    # Calculated metrics  
    win_rate: float = Field(
        0,
        description="Percentage of winning positions",
        ge=0,
        le=100
    )
    roi_balance: float = Field(
        0,
        description="ROI based on initial balance"
    )
    roi_equity: float = Field(
        0,
        description="ROI based on initial equity"
    )
    
    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification timestamp"
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )
    error_count: int = Field(
        0,
        description="Consecutive errors"
    )

    class Settings:
        """Collection settings."""
        name = "daily_performance"
        indexes = [
            [("account_id", 1), ("date", 1)],  # Compound index
            "date",                            # For date range queries
            "created_at"                       # For retention cleanup
        ]

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date is in YYYY-MM-DD format."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError as e:
            raise ValidationError(
                "Invalid date format",
                context={
                    "date": v,
                    "expected_format": "YYYY-MM-DD",
                    "error": str(e)
                }
            )

    @field_validator("trades", "winning_trades")
    @classmethod
    def validate_trade_counts(cls, v: int) -> int:
        """Ensure trade counts are non-negative."""
        if v < 0:
            raise ValidationError(
                "Trade counts must be non-negative",
                context={"value": v}
            )
        return v

    @field_validator("win_rate")
    @classmethod
    def validate_win_rate(cls, v: float) -> float:
        """Ensure win rate is between 0 and 100."""
        if not 0 <= v <= 100:
            raise ValidationError(
                "Win rate must be between 0 and 100",
                context={
                    "win_rate": v,
                    "valid_range": "0-100"
                }
            )
        return v

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate account references and metrics.
        
        Validates:
        - Account exists and is active
        - Trade counts align with win rate
        - ROI calculations valid
        """
        try:
            # Validate account exists and is active
            valid = await reference_manager.validate_reference(
                source_type="DailyPerformance",
                target_type="Account",
                reference_id=self.account_id
            )
            
            if not valid:
                raise ValidationError(
                    "Referenced account not found or inactive",
                    context={"account_id": self.account_id}
                )

            # Validate metrics
            await self.validate_balance_metrics()
            await self.validate_trade_metrics()
            await self.calculate_derived_metrics()

            # Update timestamp
            self.modified_at = datetime.utcnow()

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "account_id": self.account_id,
                    "date": self.date,
                    "error": str(e)
                }
            )

    async def update_metrics(self) -> None:
        """
        Update calculated performance metrics.
        
        Updates:
        - Win rate calculation
        - ROI calculations
        - Fee totals
        - Last updated timestamp
        
        Raises:
            ValidationError: If calculations fail
            DatabaseError: If save fails
        """
        try:
            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Calculate metrics
            metrics = await calculator.calculate_metrics(
                balance=self.closing_balance,
                equity=self.closing_equity,
                metrics={
                    "trades": self.trades,
                    "winning_trades": self.winning_trades,
                    "volume": self.volume,
                    "daily_pnl": self.daily_pnl,
                    "trading_fees": self.trading_fees,
                    "funding_fees": self.funding_fees,
                    "start_balance": self.initial_balance,
                    "start_equity": self.initial_equity
                }
            )

            # Update calculated fields
            self.win_rate = metrics.win_rate
            self.roi_balance = metrics.roi_balance
            self.roi_equity = metrics.roi_equity
            
            # Update timestamp
            self.modified_at = datetime.utcnow()
            await self.save()
            
            logger.info(
                "Updated performance metrics",
                extra={
                    "account_id": self.account_id,
                    "date": self.date,
                    "pnl": str(self.daily_pnl),
                    "roi_balance": self.roi_balance
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update metrics",
                context={
                    "account_id": self.account_id,
                    "date": self.date,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_account_metrics(
        cls,
        account_id: str,
        time_range: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Get daily performance records for a single account.
        
        Args:
            account_id: Account to get metrics for
            time_range: Dict with start_date and end_date
            
        Returns:
            Dict[str, Any]: Account metrics
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If query fails
            NotFoundError: If no data found
        """
        try:
            # Validate dates
            try:
                start = datetime.strptime(time_range["start_date"], "%Y-%m-%d")
                end = datetime.strptime(time_range["end_date"], "%Y-%m-%d") 
            except (KeyError, ValueError) as e:
                raise ValidationError(
                    "Invalid time range format",
                    context={
                        "time_range": time_range,
                        "error": str(e)
                    }
                )

            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Get records for period
            records = await cls.find({
                "account_id": account_id,
                "date": {
                    "$gte": time_range["start_date"],
                    "$lte": time_range["end_date"]
                }
            }).to_list()
            
            if not records:
                raise NotFoundError(
                    "No performance data found",
                    context={
                        "account_id": account_id,
                        "date_range": f"{start} to {end}"
                    }
                )

            # Calculate period metrics
            metrics = await calculator.calculate_period_metrics([
                r.to_dict() for r in records
            ])

            logger.info(
                "Retrieved account metrics",
                extra={
                    "account_id": account_id,
                    "date_range": f"{start} to {end}",
                    "record_count": len(records)
                }
            )

            return metrics

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get account metrics",
                context={
                    "account_id": account_id,
                    "time_range": time_range,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_daily_metrics(
        cls,
        account_id: str,
        date: str
    ) -> Dict[str, Any]:
        """
        Get performance metrics for a specific day.
        
        Args:
            account_id: Account to get metrics for
            date: Date in YYYY-MM-DD format
            
        Returns:
            Dict[str, Any]: Daily metrics
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If query fails
            NotFoundError: If no data found
        """
        try:
            # Validate date
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as e:
                raise ValidationError(
                    "Invalid date format",
                    context={
                        "date": date,
                        "expected_format": "YYYY-MM-DD",
                        "error": str(e)
                    }
                )

            # Get record for date
            record = await cls.find_one({
                "account_id": account_id,
                "date": date
            })

            if not record:
                raise NotFoundError(
                    "No performance data found",
                    context={
                        "account_id": account_id,
                        "date": date
                    }
                )

            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Calculate daily metrics
            metrics = await calculator.calculate_metrics(
                balance=record.closing_balance,
                equity=record.closing_equity,
                metrics={
                    "trades": record.trades,
                    "winning_trades": record.winning_trades,
                    "volume": record.volume,
                    "daily_pnl": record.daily_pnl,
                    "trading_fees": record.trading_fees,
                    "funding_fees": record.funding_fees,
                    "start_balance": record.initial_balance,
                    "start_equity": record.initial_equity
                }
            )

            logger.info(
                "Retrieved daily metrics",
                extra={
                    "account_id": account_id,
                    "date": date
                }
            )

            return metrics

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get daily metrics",
                context={
                    "account_id": account_id,
                    "date": date,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_risk_metrics(
        cls,
        account_id: str,
        start_date: str,
        end_date: str
    ) -> Dict[str, float]:
        """
        Get risk metrics for time period.
        
        Args:
            account_id: Account to analyze
            start_date: Start date
            end_date: End date
            
        Returns:
            Dict[str, float]: Risk metrics
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If calculation fails
        """
        try:
            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Get performance records
            records = await cls.find({
                "account_id": account_id,
                "date": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }).to_list()

            # Calculate risk metrics
            metrics = await calculator.calculate_risk_metrics([
                r.to_dict() for r in records
            ])

            logger.info(
                "Retrieved risk metrics",
                extra={
                    "account_id": account_id,
                    "date_range": f"{start_date} to {end_date}"
                }
            )

            return metrics

        except Exception as e:
            raise DatabaseError(
                "Failed to get risk metrics",
                context={
                    "account_id": account_id,
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    @classmethod
    async def get_period_metrics(
        cls,
        account_ids: List[str],
        time_range: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Get consolidated metrics for a time period.
        
        Args:
            account_ids: List of accounts to analyze
            time_range: Dict with start_date and end_date
            
        Returns:
            Dict[str, Any]: Period metrics
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If calculation fails
        """
        try:
            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Get records for period
            data = {}
            for account_id in account_ids:
                records = await cls.find({
                    "account_id": account_id,
                    "date": {
                        "$gte": time_range["start_date"],
                        "$lte": time_range["end_date"]
                    }
                }).to_list()
                
                if records:
                    data[account_id] = [r.to_dict() for r in records]

            # Calculate period metrics
            metrics = await calculator.calculate_period_metrics(data)

            logger.info(
                "Retrieved period metrics",
                extra={
                    "account_count": len(account_ids),
                    "date_range": f"{time_range['start_date']} to {time_range['end_date']}"
                }
            )

            return metrics

        except Exception as e:
            raise DatabaseError(
                "Failed to get period metrics",
                context={
                    "account_ids": account_ids,
                    "time_range": time_range,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_cumulative_metrics(
        cls,
        data: List[Dict[str, Any]],
        initial_balance: Decimal
    ) -> List[Dict[str, Any]]:
        """
        Calculate cumulative performance metrics.
        
        Args:
            data: List of daily performance records
            initial_balance: Starting balance
            
        Returns:
            List[Dict]: Cumulative metrics by day
            
        Raises:
            ValidationError: If data invalid
            DatabaseError: If calculation fails
        """
        try:
            # Get aggregator service
            aggregator = await reference_manager.get_service(
                service_type="PerformanceAggregator"
            )

            # Calculate cumulative metrics
            metrics = await aggregator.get_cumulative_metrics(
                data=data,
                initial_balance=initial_balance
            )

            logger.info(
                "Calculated cumulative metrics",
                extra={
                    "days": len(data),
                    "initial_balance": str(initial_balance)
                }
            )

            return metrics

        except Exception as e:
            raise DatabaseError(
                "Failed to get cumulative metrics",
                context={
                    "data_points": len(data),
                    "initial_balance": str(initial_balance),
                    "error": str(e)
                }
            )

    @classmethod 
    async def get_group_performance(
        cls,
        group_id: str,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get aggregated performance for an account group.
        
        Args:
            group_id: Group to analyze
            start_date: Start date
            end_date: End date
            
        Returns:
            Dict[str, Any]: Group performance metrics
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If aggregation fails
            NotFoundError: If group not found
        """
        try:
            # Get group accounts
            group = await reference_manager.get_reference(group_id)
            if not group:
                raise NotFoundError(
                    "Group not found",
                    context={"group_id": group_id}
                )

            account_ids = group.get("accounts", [])
            if not account_ids:
                raise ValidationError(
                    "Group has no accounts",
                    context={"group_id": group_id}
                )

            # Get aggregator service
            aggregator = await reference_manager.get_service(
                service_type="PerformanceAggregator"
            )

            # Get performance data
            data = {}
            for account_id in account_ids:
                records = await cls.find({
                    "account_id": account_id,
                    "date": {
                        "$gte": start_date,
                        "$lte": end_date
                    }
                }).to_list()
                
                if records:
                    data[account_id] = [r.to_dict() for r in records]

            # Get group metrics
            metrics = await aggregator.aggregate_group_metrics(data)

            logger.info(
                "Retrieved group performance",
                extra={
                    "group_id": group_id,
                    "date_range": f"{start_date} to {end_date}",
                    "account_count": len(account_ids)
                }
            )

            return metrics

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get group performance",
                context={
                    "group_id": group_id,
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    @classmethod
    async def cleanup_old_records(cls, retention_days: int) -> int:
        """
        Clean up old performance records based on retention policy.
        
        Args:
            retention_days: Number of days to retain
            
        Returns:
            int: Number of records deleted
            
        Raises:
            DatabaseError: If cleanup fails
        """
        try:
            cutoff_date = (
                datetime.utcnow() - timedelta(days=retention_days)
            ).strftime("%Y-%m-%d")
            
            result = await cls.find(
                {"date": {"$lt": cutoff_date}}
            ).delete()
            
            deleted_count = result.deleted_count
            
            logger.info(
                "Cleaned old performance records",
                extra={
                    "retention_days": retention_days,
                    "records_deleted": deleted_count
                }
            )
            
            return deleted_count
            
        except Exception as e:
            raise DatabaseError(
                "Failed to clean old records",
                context={
                    "retention_days": retention_days,
                    "error": str(e)
                }
            )

    async def validate_balance_metrics(self) -> None:
        """
        Validate balance metrics consistency.
        
        Raises:
            ValidationError: If balances invalid
        """
        try:
            if self.initial_balance <= 0:
                raise ValidationError(
                    "Initial balance must be positive",
                    context={"initial_balance": str(self.initial_balance)}
                )

            if self.initial_equity <= 0:
                raise ValidationError(
                    "Initial equity must be positive",
                    context={"initial_equity": str(self.initial_equity)}
                )

            if self.closing_balance < 0:
                raise ValidationError(
                    "Closing balance cannot be negative",
                    context={"closing_balance": str(self.closing_balance)}
                )

            if self.closing_equity < 0:
                raise ValidationError(
                    "Closing equity cannot be negative",
                    context={"closing_equity": str(self.closing_equity)}
                )

            # Check balance relationships
            if self.closing_equity < self.closing_balance:
                raise ValidationError(
                    "Equity cannot be less than balance",
                    context={
                        "closing_balance": str(self.closing_balance),
                        "closing_equity": str(self.closing_equity)
                    }
                )

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Balance validation failed",
                context={
                    "balances": {
                        "initial_balance": str(self.initial_balance),
                        "initial_equity": str(self.initial_equity),
                        "closing_balance": str(self.closing_balance),
                        "closing_equity": str(self.closing_equity)
                    },
                    "error": str(e)
                }
            )

    async def validate_trade_metrics(self) -> None:
        """
        Validate trade metrics consistency.
        
        Raises:
            ValidationError: If trade metrics invalid
        """
        try:
            if self.trades < 0:
                raise ValidationError(
                    "Trade count cannot be negative",
                    context={"trades": self.trades}
                )

            if self.winning_trades < 0:
                raise ValidationError(
                    "Winning trades cannot be negative",
                    context={"winning_trades": self.winning_trades}
                )

            if self.winning_trades > self.trades:
                raise ValidationError(
                    "Winning trades cannot exceed total trades",
                    context={
                        "trades": self.trades,
                        "winning_trades": self.winning_trades
                    }
                )

            if self.volume < 0:
                raise ValidationError(
                    "Volume cannot be negative",
                    context={"volume": str(self.volume)}
                )

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Trade metrics validation failed",
                context={
                    "metrics": {
                        "trades": self.trades,
                        "winning_trades": self.winning_trades,
                        "volume": str(self.volume)
                    },
                    "error": str(e)
                }
            )

    async def calculate_derived_metrics(self) -> None:
        """
        Calculate metrics derived from base data.
        
        Updates:
        - Win rate
        - ROI calculations
        
        Raises:
            ValidationError: If calculations fail
        """
        try:
            # Get calculator service
            calculator = await reference_manager.get_service(
                service_type="PerformanceCalculator"
            )

            # Calculate metrics
            metrics = await calculator.calculate_metrics(
                balance=self.closing_balance,
                equity=self.closing_equity,
                metrics={
                    "trades": self.trades,
                    "winning_trades": self.winning_trades,
                    "volume": self.volume,
                    "daily_pnl": self.daily_pnl,
                    "trading_fees": self.trading_fees,
                    "funding_fees": self.funding_fees,
                    "start_balance": self.initial_balance,
                    "start_equity": self.initial_equity
                }
            )

            # Update calculated fields
            self.win_rate = metrics.win_rate
            self.roi_balance = metrics.roi_balance
            self.roi_equity = metrics.roi_equity

        except Exception as e:
            raise ValidationError(
                "Metric calculation failed",
                context={
                    "account_id": self.account_id,
                    "date": self.date,
                    "error": str(e)
                }
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
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
                "trades": self.trades,
                "winning_trades": self.winning_trades,
                "volume": str(self.volume),
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
            }
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"DailyPerformance(account={self.account_id}, "
            f"date={self.date}, pnl={self.daily_pnl})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    ValidationError,
    DatabaseError,
    NotFoundError
)
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)