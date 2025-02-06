"""
Performance data storage implementation with error handling and data validation.

Features:
- Daily performance storage
- Historic data retrieval
- Cleanup operations
- Cache management 
- Error handling
- Data validation
"""

from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
from decimal import Decimal
import asyncio
from pathlib import Path

from app.core.errors.base import (
    DatabaseError,
    ValidationError, 
    NotFoundError
)
from.app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    PerformanceDict,
    PerformanceMetrics,
    DateRange
)
from app.models.daily_performance import DailyPerformance

logger = get_logger(__name__)

class PerformanceStorage:
    """
    Manages storage and retrieval of performance data.
    
    Features:
    - Store daily performance records
    - Retrieve historical data
    - Performance data validation 
    - Cleanup operations
    - Cache management
    """

    def __init__(
        self, 
        retention_days: int = 365,
        batch_size: int = 1000,
        cache_ttl: int = 3600
    ):
        """
        Initialize storage with configuration.
        
        Args:
            retention_days: Days to retain data (default 1 year)
            batch_size: Batch size for operations
            cache_ttl: Cache TTL in seconds
        """
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.cache_ttl = cache_ttl
        self.logger = logger.getChild('performance_storage')
        self._lock = asyncio.Lock()
        
    async def store_daily_performance(
        self,
        account_id: str,
        date: datetime,
        performance: PerformanceMetrics
    ) -> None:
        """
        Store daily performance record with validation.
        
        Args:
            account_id: Account ID
            date: Performance date  
            performance: Performance metrics to store
            
        Raises:
            ValidationError: If data validation fails
            DatabaseError: If storage operation fails
        """
        try:
            # Format date
            date_str = date.strftime("%Y-%m-%d")
            
            # Validate data
            await self._validate_performance_data(
                account_id=account_id,
                date=date,
                performance=performance
            )
            
            async with self._lock:
                # Try to get existing record
                record = await DailyPerformance.find_one({
                    "account_id": account_id,
                    "date": date_str
                })
                
                if record:
                    # Update existing record
                    for key, value in performance.dict().items():
                        if hasattr(record, key):
                            setattr(record, key, value)
                            
                    record.modified_at = datetime.utcnow()
                    
                else:
                    # Create new record
                    record = DailyPerformance(
                        account_id=account_id,
                        date=date_str,
                        **performance.dict()
                    )

                await record.save()
                
                self.logger.info(
                    "Stored daily performance",
                    extra={
                        "account_id": account_id,
                        "date": date_str,
                        "metrics": performance.dict()
                    }
                )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to store daily performance",
                context={
                    "account_id": account_id,
                    "date": date.isoformat(),
                    "error": str(e)
                }
            )
            
    async def get_performance_data(
        self,
        account_id: str,
        date_range: DateRange
    ) -> List[PerformanceDict]:
        """
        Get performance records for a date range.
        
        Args:
            account_id: Account ID
            date_range: Time range for data
            
        Returns:
            List of performance records
            
        Raises:
            ValidationError: If date range invalid
            DatabaseError: If query fails 
            NotFoundError: If no data found
        """
        try:
            # Validate date range
            if date_range.start_date > date_range.end_date:
                raise ValidationError(
                    "Invalid date range",
                    context={
                        "start_date": date_range.start_date,
                        "end_date": date_range.end_date
                    }
                )
                
            # Query records
            records = await DailyPerformance.find({
                "account_id": account_id,
                "date": {
                    "$gte": date_range.start_date,
                    "$lte": date_range.end_date
                }
            }).sort("date").to_list()
            
            if not records:
                raise NotFoundError(
                    "No performance data found",
                    context={
                        "account_id": account_id,
                        "date_range": f"{date_range.start_date} to {date_range.end_date}"
                    }
                )
                
            # Convert to dicts and serialize decimals
            result = []
            for record in records:
                data = record.dict()
                # Convert Decimal values to float
                for key, value in data.items():
                    if isinstance(value, Decimal):
                        data[key] = float(value)
                result.append(data)
                
            self.logger.info(
                "Retrieved performance data",
                extra={
                    "account_id": account_id,
                    "date_range": f"{date_range.start_date} to {date_range.end_date}",
                    "record_count": len(result)
                }
            )
                
            return result

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get performance data",
                context={
                    "account_id": account_id,
                    "date_range": f"{date_range.start_date} to {date_range.end_date}",
                    "error": str(e)
                }
            )
            
    async def cleanup_old_records(
        self,
        retention_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Clean up old performance records.
        
        Args:
            retention_days: Optional override of default retention
            
        Returns:
            Dict with cleanup results
            
        Raises:
            DatabaseError: If cleanup fails
        """
        try:
            days = retention_days or self.retention_days
            cutoff = datetime.utcnow() - timedelta(days=days)
            cutoff_str = cutoff.strftime("%Y-%m-%d")
            
            async with self._lock:
                # Delete old records in batches
                total_deleted = 0
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
            
            self.logger.info(
                "Cleaned up old performance records",
                extra=cleanup_result
            )
            
            return cleanup_result

        except Exception as e:
            raise DatabaseError(
                "Failed to cleanup old records",
                context={
                    "retention_days": retention_days,
                    "error": str(e)
                }
            )
            
    async def get_latest_performance(
        self,
        account_id: str
    ) -> Optional[PerformanceDict]:
        """
        Get most recent performance record.
        
        Args:
            account_id: Account ID
            
        Returns:
            Latest performance record or None if not found
            
        Raises:
            DatabaseError: If query fails
        """
        try:
            record = await DailyPerformance.find({
                "account_id": account_id
            }).sort("-date").first()
            
            if not record:
                return None
                
            # Convert to dict and serialize decimals
            data = record.dict()
            for key, value in data.items():
                if isinstance(value, Decimal):
                    data[key] = float(value)
                    
            self.logger.debug(
                "Retrieved latest performance",
                extra={
                    "account_id": account_id,
                    "date": record.date
                }
            )
                
            return data

        except Exception as e:
            raise DatabaseError(
                "Failed to get latest performance",
                context={
                    "account_id": account_id,
                    "error": str(e)
                }
            )
            
    async def _validate_performance_data(
        self,
        account_id: str,
        date: datetime,
        performance: PerformanceMetrics
    ) -> None:
        """
        Validate performance data before storage.
        
        Args:
            account_id: Account ID
            date: Performance date
            performance: Performance metrics
            
        Raises:
            ValidationError: If validation fails
        """
        try:
            # Check required fields
            required = [
                "trades",
                "volume",
                "pnl",
                "fees",
                "roi",
                "balance",
                "equity"
            ]
            
            missing = [
                field for field in required 
                if getattr(performance, field, None) is None
            ]
            
            if missing:
                raise ValidationError(
                    "Missing required performance fields",
                    context={
                        "account_id": account_id,
                        "missing_fields": missing
                    }
                )
                
            # Validate trade counts
            if performance.trades < 0:
                raise ValidationError(
                    "Trade count cannot be negative",
                    context={
                        "trades": performance.trades
                    }
                )
                
            # Validate balance/equity
            if performance.balance <= 0:
                raise ValidationError(
                    "Balance must be positive",
                    context={
                        "balance": str(performance.balance)
                    }
                )
                
            if performance.equity < 0:
                raise ValidationError(
                    "Equity cannot be negative", 
                    context={
                        "equity": str(performance.equity)
                    }
                )
                
            # Validate date not in future
            if date > datetime.utcnow():
                raise ValidationError(
                    "Performance date cannot be in future",
                    context={
                        "date": date.isoformat()
                    }
                )

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Performance data validation failed",
                context={
                    "account_id": account_id,
                    "date": date.isoformat(),
                    "error": str(e)
                }
            )