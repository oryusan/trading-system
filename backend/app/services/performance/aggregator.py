"""
Performance data aggregation implementation.

Features:
- Time-based aggregation 
- Group aggregation
- Cumulative metrics
- Performance timeseries
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from decimal import Decimal
import pytz
from collections import defaultdict

from app.core.errors.base import ValidationError
from app.core.logging.logger import get_logger
from app.core.references import (
    PerformanceMetrics,
    TimeSeriesData,
    PerformanceDict
)

logger = get_logger(__name__)

class PerformanceAggregator:
    """
    Aggregates performance data across time periods and accounts.
    
    Features:
    - Time interval aggregation
    - Group performance aggregation
    - Cumulative metrics calculation
    - Performance timeseries generation
    """

    def __init__(self):
        """Initialize aggregator with timezone."""
        self.logger = logger.getChild('performance_aggregator')
        self.timezone = pytz.UTC

    async def aggregate_by_interval(
        self,
        data: Dict[str, List[PerformanceDict]],
        interval: str = "day"
    ) -> TimeSeriesData:
        """
        Aggregate performance data by time interval.
        
        Args:
            data: Dict mapping account IDs to performance records
            interval: Aggregation interval (day/week/month)
            
        Returns:
            TimeSeriesData containing aggregated metrics by timestamp
            
        Raises:
            ValidationError: If aggregation fails
        """
        try:
            if not data:
                raise ValidationError(
                    "No data to aggregate",
                    context={"interval": interval}
                )

            # Validate interval
            valid_intervals = {"day", "week", "month", "quarter"}
            if interval not in valid_intervals:
                raise ValidationError(
                    "Invalid interval",
                    context={
                        "interval": interval,
                        "valid_intervals": list(valid_intervals)
                    }
                )

            # Initialize aggregation
            aggregated: Dict[datetime, Dict[str, Any]] = defaultdict(
                lambda: {
                    "trades": 0,
                    "winning_trades": 0,
                    "volume": Decimal('0'),
                    "trading_fees": Decimal('0'),
                    "funding_fees": Decimal('0'),
                    "pnl": Decimal('0'),
                    "balance": Decimal('0'),
                    "equity": Decimal('0'),
                    "account_count": 0
                }
            )

            # Aggregate by interval
            for account_id, records in data.items():
                for record in records:
                    # Get interval timestamp
                    timestamp = self._get_interval_timestamp(
                        datetime.fromisoformat(record['date']),
                        interval
                    )

                    # Update metrics
                    agg = aggregated[timestamp]
                    agg['trades'] += record.get('trades', 0)
                    agg['winning_trades'] += record.get('winning_trades', 0)
                    agg['volume'] += Decimal(str(record.get('volume', 0)))
                    agg['trading_fees'] += Decimal(str(record.get('trading_fees', 0)))
                    agg['funding_fees'] += Decimal(str(record.get('funding_fees', 0)))
                    agg['pnl'] += Decimal(str(record.get('pnl', 0)))
                    agg['balance'] += Decimal(str(record.get('balance', 0)))
                    agg['equity'] += Decimal(str(record.get('equity', 0)))
                    agg['account_count'] += 1

            # Calculate averages and ratios
            results = {}
            for timestamp, agg in aggregated.items():
                account_count = agg['account_count']
                if account_count > 0:
                    metrics = {
                        "trades": agg['trades'],
                        "winning_trades": agg['winning_trades'],
                        "volume": float(agg['volume']),
                        "trading_fees": float(agg['trading_fees']),
                        "funding_fees": float(agg['funding_fees']),
                        "pnl": float(agg['pnl']),
                        "balance": float(agg['balance'] / account_count),
                        "equity": float(agg['equity'] / account_count),
                        "account_count": account_count
                    }

                    # Calculate ratios
                    if agg['trades'] > 0:
                        metrics['win_rate'] = round(
                            agg['winning_trades'] / agg['trades'] * 100,
                            2
                        )
                    else:
                        metrics['win_rate'] = 0.0

                    results[timestamp] = metrics

            self.logger.debug(
                "Aggregated performance by interval",
                extra={
                    "interval": interval,
                    "account_count": len(data),
                    "period_count": len(results)
                }
            )

            return results

        except Exception as e:
            raise ValidationError(
                "Aggregation failed",
                context={
                    "interval": interval,
                    "account_count": len(data),
                    "error": str(e)
                }
            )

    async def aggregate_group_metrics(
        self,
        data: Dict[str, List[PerformanceDict]]
    ) -> PerformanceMetrics:
        """
        Aggregate metrics across group accounts.
        
        Args:
            data: Dict mapping account IDs to performance records
            
        Returns:
            PerformanceMetrics containing aggregated group metrics
            
        Raises:
            ValidationError: If aggregation fails
        """
        try:
            if not data:
                raise ValidationError(
                    "No data to aggregate",
                    context={"account_count": 0}
                )

            # Initialize aggregates
            totals = {
                "trades": 0,
                "winning_trades": 0,
                "volume": Decimal('0'),
                "trading_fees": Decimal('0'),
                "funding_fees": Decimal('0'),
                "pnl": Decimal('0')
            }

            # Track initial values
            first_records = [records[0] for records in data.values() if records]
            start_balance = sum(
                Decimal(str(r.get('balance', 0))) 
                for r in first_records
            )

            # Track final values
            last_records = [records[-1] for records in data.values() if records]
            end_balance = sum(
                Decimal(str(r.get('balance', 0)))
                for r in last_records
            )

            # Aggregate metrics across all accounts
            for records in data.values():
                for record in records:
                    totals['trades'] += record.get('trades', 0)
                    totals['winning_trades'] += record.get('winning_trades', 0)
                    totals['volume'] += Decimal(str(record.get('volume', 0)))
                    totals['trading_fees'] += Decimal(str(record.get('trading_fees', 0)))
                    totals['funding_fees'] += Decimal(str(record.get('funding_fees', 0)))
                    totals['pnl'] += Decimal(str(record.get('pnl', 0)))

            # Calculate group metrics
            metrics_dict = {
                "start_balance": start_balance,
                "end_balance": end_balance,
                "total_trades": totals['trades'],
                "winning_trades": totals['winning_trades'],
                "total_volume": totals['volume'],
                "trading_fees": totals['trading_fees'],
                "funding_fees": totals['funding_fees'],
                "total_pnl": totals['pnl']
            }

            # Calculate ratios
            if totals['trades'] > 0:
                metrics_dict["win_rate"] = round(
                    totals['winning_trades'] / totals['trades'] * 100,
                    2
                )
            else:
                metrics_dict["win_rate"] = 0.0

            # Calculate ROI
            if start_balance > 0:
                roi = ((end_balance - start_balance) / start_balance * 100)
                metrics_dict["roi"] = round(float(roi), 2)
            else:
                metrics_dict["roi"] = 0.0

            # Calculate drawdown
            all_equities = []
            for records in data.values():
                for record in records:
                    all_equities.append(Decimal(str(record.get('equity', 0))))

            if all_equities:
                max_equity = max(all_equities)
                if max_equity > 0:
                    min_equity = min(all_equities)
                    drawdown = ((max_equity - min_equity) / max_equity * 100)
                    metrics_dict["drawdown"] = round(float(drawdown), 2)
                else:
                    metrics_dict["drawdown"] = 0.0
            else:
                metrics_dict["drawdown"] = 0.0

            # Create PerformanceMetrics instance
            try:
                performance = PerformanceMetrics(**metrics_dict)
            except ValidationError as e:
                raise ValidationError(
                    "Invalid group metrics",
                    context={
                        "metrics": metrics_dict,
                        "error": str(e)
                    }
                )

            self.logger.debug(
                "Aggregated group metrics",
                extra={
                    "account_count": len(data),
                    "total_trades": totals['trades'],
                    "total_pnl": str(totals['pnl'])
                }
            )

            return performance

        except Exception as e:
            raise ValidationError(
                "Group aggregation failed",
                context={
                    "account_count": len(data),
                    "error": str(e)
                }
            )

    def _get_interval_timestamp(
        self,
        dt: datetime,
        interval: str
    ) -> datetime:
        """Get normalized timestamp for interval."""
        dt = dt.astimezone(self.timezone)
        
        if interval == "day":
            return dt.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )
            
        elif interval == "week":
            # Start week on Monday
            return (dt - timedelta(days=dt.weekday())).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )
            
        elif interval == "month":
            return dt.replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )
            
        elif interval == "quarter":
            quarter_start = ((dt.month - 1) // 3) * 3 + 1
            return dt.replace(
                month=quarter_start,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )
            
        else:
            raise ValueError(f"Invalid interval: {interval}")

    async def get_cumulative_metrics(
        self,
        data: List[PerformanceDict],
        initial_balance: Decimal
    ) -> List[Dict[str, Any]]:
        """
        Calculate cumulative performance metrics for a time series.
        
        Args:
            data: Daily performance records
            initial_balance: Starting balance
            
        Returns:
            List of dictionaries containing cumulative metrics
            
        Raises:
            ValidationError: If calculations fail
        """
        try:
            if not data:
                raise ValidationError(
                    "No data for cumulative metrics",
                    context={"data_length": 0}
                )

            results = []
            running_pnl = Decimal('0')
            running_volume = Decimal('0')
            running_fees = Decimal('0')
            running_trades = 0
            running_wins = 0
            high_balance = initial_balance

            for record in data:
                # Update running totals
                running_pnl += Decimal(str(record.get('pnl', 0)))
                running_volume += Decimal(str(record.get('volume', 0)))
                running_fees += Decimal(str(record.get('trading_fees', 0)))
                running_fees += Decimal(str(record.get('funding_fees', 0)))
                running_trades += record.get('trades', 0)
                running_wins += record.get('winning_trades', 0)

                # Calculate metrics
                current_balance = Decimal(str(record.get('balance', 0)))
                high_balance = max(high_balance, current_balance)

                cumulative = {
                    "date": record['date'],
                    "balance": float(current_balance),
                    "cumulative_pnl": float(running_pnl),
                    "cumulative_volume": float(running_volume),
                    "cumulative_fees": float(running_fees),
                    "total_trades": running_trades,
                    "win_rate": (round(running_wins / running_trades * 100, 2)
                               if running_trades > 0 else 0.0),
                    "roi": float(
                        round(
                            ((current_balance - initial_balance) / initial_balance * 100),
                            2
                        )
                    ) if initial_balance > 0 else 0.0,
                    "drawdown": float(
                        round(
                            ((high_balance - current_balance) / high_balance * 100),
                            2
                        )
                    ) if high_balance > 0 else 0.0
                }
                
                results.append(cumulative)

            self.logger.debug(
                "Calculated cumulative metrics",
                extra={
                    "data_points": len(results),
                    "final_pnl": str(running_pnl),
                    "total_trades": running_trades
                }
            )

            return results

        except Exception as e:
            raise ValidationError(
                "Cumulative metrics calculation failed",
                context={
                    "data_length": len(data),
                    "initial_balance": str(initial_balance),
                    "error": str(e)
                }
            )