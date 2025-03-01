"""
Performance data storage implementation with error handling and data validation.

Features:
- Daily performance storage (based on closed, realized trades)
- Historic data retrieval
- Cleanup operations
- Cache management 
- Error handling
- Data validation

The stored documents are expected to include fields such as:
  - "closed_trades": Count of finalized (closed) trades.
  - "closed_trade_value": Total notional value of closed trades.
  - "trading_fees": Total fees incurred (trading fees).
  - "funding_fees": Total funding fees.
  - "total_pnl": Gross profit/loss from closed trades.
  - "balance", "equity", "roi": Financial snapshot data.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.logging.logger import get_logger
from app.core.references import PerformanceDict, PerformanceMetrics, DateRange
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.decorators import error_handler

logger = get_logger(__name__)


class PerformanceStorage:
    """
    Manages storage and retrieval of performance data.

    This module stores daily performance records that are computed using only closed (realized) trade data.
    """

    def __init__(
        self, 
        retention_days: int = 365,
        batch_size: int = 1000,
        cache_ttl: int = 3600
    ) -> None:
        """
        Initialize storage with configuration.

        Args:
            retention_days: Days to retain data (default: 1 year).
            batch_size: Batch size for deletion operations.
            cache_ttl: Cache time-to-live in seconds.
        """
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.cache_ttl = cache_ttl
        self.logger = get_logger("performance_storage")
        self._lock = asyncio.Lock()

    @staticmethod
    def _serialize_record(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Decimal values in a record to float.

        Args:
            data: A dictionary representing the record.

        Returns:
            A new dictionary with Decimal values converted to float.
        """
        return {k: float(v) if isinstance(v, Decimal) else v for k, v in data.items()}

    @error_handler(
        context_extractor=lambda self, account_id, date, performance: {
            "account_id": account_id,
            "date": date.strftime("%Y-%m-%d"),
            "metrics": performance.dict()
        },
        log_message="Failed to store daily performance"
    )
    async def store_daily_performance(
        self,
        account_id: str,
        date: datetime,
        performance: PerformanceMetrics
    ) -> None:
        """
        Store a daily performance record after validating the data.

        Args:
            account_id: Account ID.
            date: Performance date (typically set to midnight UTC for the day).
            performance: Performance metrics to store.
        """
        date_str = date.strftime("%Y-%m-%d")
        await self._validate_performance_data(account_id, date, performance)

        async with self._lock:
            record = await DailyPerformance.find_one({
                "account_id": account_id,
                "date": date_str
            })
            if record:
                # Update existing record
                for key, value in performance.dict().items():
                    setattr(record, key, value)
                record.modified_at = datetime.utcnow()
            else:
                record = DailyPerformance(account_id=account_id, date=date_str, **performance.dict())
            await record.save()

        self.logger.info(
            "Stored daily performance",
            extra={"account_id": account_id, "date": date_str, "metrics": performance.dict()}
        )

    @error_handler(
        context_extractor=lambda self, account_id, date_range: {
            "account_id": account_id,
            "start_date": date_range.start_date.strftime("%Y-%m-%d"),
            "end_date": date_range.end_date.strftime("%Y-%m-%d")
        },
        log_message="Failed to retrieve performance data"
    )
    async def get_performance_data(
        self,
        account_id: str,
        date_range: DateRange
    ) -> List[PerformanceDict]:
        """
        Get performance records for a specified date range.

        Args:
            account_id: Account ID.
            date_range: Time range for the data.
        Returns:
            A list of performance records.
        Raises:
            ValidationError: If the date range is invalid.
            NotFoundError: If no data is found.
        """
        if date_range.start_date > date_range.end_date:
            raise ValidationError(
                "Invalid date range",
                context={
                    "start_date": date_range.start_date.strftime("%Y-%m-%d"),
                    "end_date": date_range.end_date.strftime("%Y-%m-%d")
                }
            )
        records = await DailyPerformance.find({
            "account_id": account_id,
            "date": {
                "$gte": date_range.start_date.strftime("%Y-%m-%d"),
                "$lte": date_range.end_date.strftime("%Y-%m-%d")
            }
        }).sort("date").to_list()
        if not records:
            raise NotFoundError(
                "No performance data found",
                context={
                    "account_id": account_id,
                    "date_range": f"{date_range.start_date.strftime('%Y-%m-%d')} to {date_range.end_date.strftime('%Y-%m-%d')}"
                }
            )
        result = [self._serialize_record(record.dict()) for record in records]
        self.logger.info(
            "Retrieved performance data",
            extra={
                "account_id": account_id,
                "date_range": f"{date_range.start_date.strftime('%Y-%m-%d')} to {date_range.end_date.strftime('%Y-%m-%d')}",
                "record_count": len(result)
            }
        )
        return result

    @error_handler(
        context_extractor=lambda self, retention_days=None: {"retention_days": retention_days or self.retention_days},
        log_message="Failed to cleanup old performance records"
    )
    async def cleanup_old_records(
        self,
        retention_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Clean up old performance records that exceed the retention period.

        Args:
            retention_days: Optional override for the retention period.
        Returns:
            A dictionary with the cleanup results.
        """
        days = retention_days or self.retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        total_deleted = 0
        async with self._lock:
            while True:
                result = await DailyPerformance.find({
                    "date": {"$lt": cutoff_str}
                }).limit(self.batch_size).delete()
                deleted = result.deleted_count if result else 0
                total_deleted += deleted
                if deleted < self.batch_size:
                    break
        cleanup_result = {
            "retention_days": days,
            "cutoff_date": cutoff_str,
            "deleted_count": total_deleted
        }
        self.logger.info("Cleaned up old performance records", extra=cleanup_result)
        return cleanup_result

    @error_handler(
        context_extractor=lambda self, account_id: {"account_id": account_id},
        log_message="Failed to retrieve latest performance record"
    )
    async def get_latest_performance(
        self,
        account_id: str
    ) -> Optional[PerformanceDict]:
        """
        Get the most recent performance record.

        Args:
            account_id: Account ID.
        Returns:
            The latest performance record or None if not found.
        Raises:
            DatabaseError: If the query fails.
        """
        record = await DailyPerformance.find({
            "account_id": account_id
        }).sort("-date").first()
        if not record:
            return None
        data = self._serialize_record(record.dict())
        self.logger.debug(
            "Retrieved latest performance",
            extra={"account_id": account_id, "date": record.date}
        )
        return data

    @error_handler(
        context_extractor=lambda self, account_id, date, performance: {
            "account_id": account_id,
            "date": date.strftime("%Y-%m-%d")
        },
        log_message="Performance data validation failed"
    )
    async def _validate_performance_data(
        self,
        account_id: str,
        date: datetime,
        performance: PerformanceMetrics
    ) -> None:
        """
        Validate the performance data before storage.

        This validation expects the daily record to include data derived exclusively from closed (realized)
        trades. Required fields are:
          - "closed_trades"
          - "closed_trade_value"
          - "total_pnl"
          - "trading_fees"
          - "funding_fees"
          - "roi"
          - "balance"
          - "equity"
        Args:
            account_id: Account ID.
            date: The performance date.
            performance: The performance metrics.
        Raises:
            ValidationError: If validation fails.
        """
        required_fields = [
            "closed_trades",
            "closed_trade_value",
            "total_pnl",
            "trading_fees",
            "funding_fees",
            "roi",
            "balance",
            "equity"
        ]
        missing = [field for field in required_fields if getattr(performance, field, None) is None]
        if missing:
            raise ValidationError(
                "Missing required performance fields",
                context={"account_id": account_id, "missing_fields": missing}
            )
        if performance.closed_trades < 0:
            raise ValidationError(
                "Closed trades count cannot be negative",
                context={"closed_trades": performance.closed_trades}
            )
        if performance.balance <= 0:
            raise ValidationError(
                "Balance must be positive",
                context={"balance": str(performance.balance)}
            )
        if performance.equity < 0:
            raise ValidationError(
                "Equity cannot be negative",
                context={"equity": str(performance.equity)}
            )
        if date > datetime.utcnow():
            raise ValidationError(
                "Performance date cannot be in the future",
                context={"date": date.strftime("%Y-%m-%d")}
            )
