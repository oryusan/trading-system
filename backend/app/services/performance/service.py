"""
Performance tracking and calculation service.

Features:
- Daily performance tracking
- Period aggregation (daily/weekly/monthly)
- Metric calculations
- Storage management
- Error handling
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from decimal import Decimal

from app.core.errors.base import (
    DatabaseError,
    ValidationError,
    NotFoundError
)
from.app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    PerformanceMetrics,
    DateRange,
    PerformanceDict,
    TimeSeriesData
)

from .calculator import PerformanceCalculator
from .aggregator import PerformanceAggregator
from .storage import PerformanceStorage

logger = get_logger(__name__)

class PerformanceService:
    """
    Service for managing and calculating performance metrics.
    
    Features:
    - Performance tracking and storage
    - Period-based aggregation
    - Metric calculation
    - Data validation
    """

    def __init__(self):
        """Initialize performance service components."""
        try:
            self.calculator = PerformanceCalculator()
            self.aggregator = PerformanceAggregator()
            self.storage = PerformanceStorage()
            self.logger = logger.getChild('performance_service')
            
        except Exception as e:
            raise ServiceError(
                "Failed to initialize performance service",
                context={"error": str(e)}
            )

    async def update_daily_performance(
        self,
        account_id: str,
        date: datetime,
        balance: Decimal,
        equity: Decimal,
        metrics: PerformanceDict
    ) -> None:
        """
        Update daily performance record.
        
        Args:
            account_id: Account to update
            date: Performance date
            balance: Current balance
            equity: Current equity 
            metrics: Additional metrics to store
            
        Raises:
            ValidationError: If data validation fails
            DatabaseError: If storage fails
        """
        try:
            # Validate data
            if balance <= 0 or equity <= 0:
                raise ValidationError(
                    "Invalid balance/equity values",
                    context={
                        "balance": str(balance),
                        "equity": str(equity)
                    }
                )

            # Calculate performance metrics
            performance = await self.calculator.calculate_metrics(
                balance=balance,
                equity=equity,
                metrics=metrics
            )

            # Store performance data
            await self.storage.store_daily_performance(
                account_id=account_id,
                date=date,
                performance=performance
            )

            self.logger.info(
                "Updated daily performance",
                extra={
                    "account_id": account_id,
                    "date": date.isoformat(),
                    "balance": str(balance)
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update daily performance",
                context={
                    "account_id": account_id,
                    "date": date.isoformat(),
                    "error": str(e)
                }
            )

    async def get_account_metrics(
        self,
        account_id: str,
        time_range: DateRange
    ) -> PerformanceMetrics:
        """
        Get account performance metrics for a time range.
        
        Args:
            account_id: Account to get metrics for
            time_range: Time range to analyze
            
        Returns:
            PerformanceMetrics containing calculated metrics
            
        Raises:
            NotFoundError: If account or data not found
            ValidationError: If parameters invalid
        """
        try:
            # Get raw performance data
            data = await self.storage.get_performance_data(
                account_id=account_id,
                start_date=time_range.start_date,
                end_date=time_range.end_date
            )

            if not data:
                raise NotFoundError(
                    "No performance data found",
                    context={
                        "account_id": account_id,
                        "time_range": f"{time_range.start_date} to {time_range.end_date}"
                    }
                )

            # Calculate period metrics
            metrics = await self.calculator.calculate_period_metrics(data)

            self.logger.info(
                "Retrieved account metrics",
                extra={
                    "account_id": account_id,
                    "time_range": f"{time_range.start_date} to {time_range.end_date}"
                }
            )

            return metrics

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get account metrics",
                context={
                    "account_id": account_id,
                    "time_range": f"{time_range.start_date} to {time_range.end_date}",
                    "error": str(e)
                }
            )

    async def get_group_metrics(
        self,
        group_id: str,
        time_range: DateRange
    ) -> PerformanceMetrics:
        """
        Get aggregated performance metrics for a group.
        
        Args:
            group_id: Group to get metrics for
            time_range: Time range to analyze
            
        Returns:
            PerformanceMetrics containing aggregated metrics
            
        Raises:
            NotFoundError: If group or data not found
            ValidationError: If parameters invalid
        """
        try:
            # Get group account IDs from reference manager
            from app.services.reference.manager import reference_manager
            accounts = await ref_manager.get_references(
                source_type="Group",
                reference_id=group_id
            )

            if not accounts:
                raise NotFoundError(
                    "No accounts found for group",
                    context={"group_id": group_id}
                )

            # Get performance data for all accounts
            account_data = {}
            for account in accounts:
                data = await self.storage.get_performance_data(
                    account_id=str(account["id"]),
                    start_date=time_range.start_date,
                    end_date=time_range.end_date
                )
                if data:
                    account_data[str(account["id"])] = data

            # Aggregate metrics across accounts
            metrics = await self.aggregator.aggregate_group_metrics(account_data)

            self.logger.info(
                "Retrieved group metrics",
                extra={
                    "group_id": group_id,
                    "account_count": len(account_data),
                    "time_range": f"{time_range.start_date} to {time_range.end_date}"
                }
            )

            return metrics

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get group metrics",
                context={
                    "group_id": group_id,
                    "time_range": f"{time_range.start_date} to {time_range.end_date}",
                    "error": str(e)
                }
            )

    async def aggregate_performance(
        self,
        account_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        interval: str = "day"
    ) -> TimeSeriesData:
        """
        Get time series performance data for accounts.
        
        Args:
            account_ids: Accounts to aggregate
            start_date: Start of time range
            end_date: End of time range
            interval: Aggregation interval
            
        Returns:
            TimeSeriesData containing aggregated metrics by timestamp
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If aggregation fails
        """
        try:
            # Validate parameters
            if not account_ids:
                raise ValidationError(
                    "No accounts provided",
                    context={
                        "account_ids": account_ids,
                        "interval": interval
                    }
                )

            if start_date >= end_date:
                raise ValidationError(
                    "Invalid date range",
                    context={
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat()
                    }
                )

            # Get performance data for all accounts
            account_data = {}
            for account_id in account_ids:
                data = await self.storage.get_performance_data(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date
                )
                if data:
                    account_data[account_id] = data

            # Aggregate data by interval
            aggregated = await self.aggregator.aggregate_by_interval(
                data=account_data,
                interval=interval
            )

            self.logger.info(
                "Aggregated performance data",
                extra={
                    "account_count": len(account_ids),
                    "interval": interval,
                    "time_range": f"{start_date.isoformat()} to {end_date.isoformat()}"
                }
            )

            return aggregated

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to aggregate performance",
                context={
                    "account_count": len(account_ids),
                    "interval": interval,
                    "error": str(e)
                }
            )

# Circular import guard
from app.core.errors import ServiceError

# Create global instance
performance_service = PerformanceService()