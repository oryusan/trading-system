"""
Performance data aggregation implementation.

Features:
- Time-based aggregation 
- Group aggregation
- Cumulative metrics calculation
- Performance timeseries generation
"""

from typing import Dict, List, Any
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict
import pytz

from app.core.errors.base import ValidationError
from app.core.errors.decorators import error_handler
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

    def __init__(self) -> None:
        """Initialize aggregator with timezone."""
        self.logger = get_logger("performance_aggregator")
        self.timezone = pytz.UTC

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        """
        Convert a value to a Decimal.

        Args:
            value: The value to convert.

        Returns:
            A Decimal representation of the value.
        """
        return Decimal(str(value or 0))

    @error_handler(
        context_extractor=lambda self, data, interval="day": {
            "interval": interval,
            "data_length": sum(len(r) for r in data.values())
        },
        log_message="Aggregation by interval failed"
    )
    async def aggregate_by_interval(
        self,
        data: Dict[str, List[PerformanceDict]],
        interval: str = "day",
    ) -> TimeSeriesData:
        """
        Aggregate performance data by time interval.

        Args:
            data: Mapping of account IDs to performance records.
            interval: Aggregation interval (day/week/month/quarter).

        Returns:
            Aggregated time series data keyed by normalized timestamp.

        Raises:
            ValidationError: If aggregation fails or an invalid interval is provided.
        """
        if not data:
            raise ValidationError("No data to aggregate", context={"interval": interval})

        valid_intervals = {"day", "week", "month", "quarter"}
        if interval not in valid_intervals:
            raise ValidationError(
                "Invalid interval",
                context={"interval": interval, "valid_intervals": list(valid_intervals)},
            )

        aggregated: Dict[datetime, Dict[str, Any]] = defaultdict(lambda: {
            "trades": 0,
            "winning_trades": 0,
            "volume": Decimal("0"),
            "trading_fees": Decimal("0"),
            "funding_fees": Decimal("0"),
            "pnl": Decimal("0"),
            "balance": Decimal("0"),
            "equity": Decimal("0"),
            "account_count": 0,
        })

        for _, records in data.items():
            for record in records:
                record_date = datetime.fromisoformat(record["date"])
                timestamp = self._get_interval_timestamp(record_date, interval)
                agg = aggregated[timestamp]
                agg["trades"] += record.get("trades", 0)
                agg["winning_trades"] += record.get("winning_trades", 0)
                agg["volume"] += self._to_decimal(record.get("volume", 0))
                agg["trading_fees"] += self._to_decimal(record.get("trading_fees", 0))
                agg["funding_fees"] += self._to_decimal(record.get("funding_fees", 0))
                agg["pnl"] += self._to_decimal(record.get("pnl", 0))
                agg["balance"] += self._to_decimal(record.get("balance", 0))
                agg["equity"] += self._to_decimal(record.get("equity", 0))
                agg["account_count"] += 1

        results: TimeSeriesData = {}
        for timestamp, agg in aggregated.items():
            account_count = agg["account_count"]
            if account_count:
                balance_avg = agg["balance"] / account_count
                equity_avg = agg["equity"] / account_count
                win_rate = (
                    round(agg["winning_trades"] / agg["trades"] * 100, 2)
                    if agg["trades"] > 0
                    else 0.0
                )
                results[timestamp] = {
                    "trades": agg["trades"],
                    "winning_trades": agg["winning_trades"],
                    "volume": float(agg["volume"]),
                    "trading_fees": float(agg["trading_fees"]),
                    "funding_fees": float(agg["funding_fees"]),
                    "pnl": float(agg["pnl"]),
                    "balance": float(balance_avg),
                    "equity": float(equity_avg),
                    "account_count": account_count,
                    "win_rate": win_rate,
                }

        self.logger.debug(
            "Aggregated performance by interval",
            extra={
                "interval": interval,
                "account_count": sum(len(r) for r in data.values()),
                "period_count": len(results),
            },
        )
        return results

    @error_handler(
        context_extractor=lambda self, data: {"account_count": len(data)},
        log_message="Group aggregation failed"
    )
    async def aggregate_group_metrics(
        self,
        data: Dict[str, List[PerformanceDict]],
    ) -> PerformanceMetrics:
        """
        Aggregate metrics across group accounts.

        Args:
            data: Mapping of account IDs to performance records.

        Returns:
            Aggregated group metrics as a PerformanceMetrics instance.

        Raises:
            ValidationError: If aggregation fails.
        """
        if not data:
            raise ValidationError("No data to aggregate", context={"account_count": 0})

        totals = {
            "trades": 0,
            "winning_trades": 0,
            "volume": Decimal("0"),
            "trading_fees": Decimal("0"),
            "funding_fees": Decimal("0"),
            "pnl": Decimal("0"),
        }

        first_records = [records[0] for records in data.values() if records]
        start_balance = sum(self._to_decimal(r.get("balance", 0)) for r in first_records)
        last_records = [records[-1] for records in data.values() if records]
        end_balance = sum(self._to_decimal(r.get("balance", 0)) for r in last_records)

        for records in data.values():
            for record in records:
                totals["trades"] += record.get("trades", 0)
                totals["winning_trades"] += record.get("winning_trades", 0)
                totals["volume"] += self._to_decimal(record.get("volume", 0))
                totals["trading_fees"] += self._to_decimal(record.get("trading_fees", 0))
                totals["funding_fees"] += self._to_decimal(record.get("funding_fees", 0))
                totals["pnl"] += self._to_decimal(record.get("pnl", 0))

        metrics_dict = {
            "start_balance": start_balance,
            "end_balance": end_balance,
            "total_trades": totals["trades"],
            "winning_trades": totals["winning_trades"],
            "total_volume": totals["volume"],
            "trading_fees": totals["trading_fees"],
            "funding_fees": totals["funding_fees"],
            "total_pnl": totals["pnl"],
            "win_rate": (
                round(totals["winning_trades"] / totals["trades"] * 100, 2)
                if totals["trades"] > 0
                else 0.0
            ),
        }

        if start_balance > 0:
            metrics_dict["roi"] = round(float((end_balance - start_balance) / start_balance * 100), 2)
        else:
            metrics_dict["roi"] = 0.0

        equities = [
            self._to_decimal(record.get("equity", 0))
            for records in data.values()
            for record in records
        ]
        if equities:
            max_equity = max(equities)
            min_equity = min(equities)
            metrics_dict["drawdown"] = (
                round(float((max_equity - min_equity) / max_equity * 100), 2)
                if max_equity > 0
                else 0.0
            )
        else:
            metrics_dict["drawdown"] = 0.0

        try:
            performance = PerformanceMetrics(**metrics_dict)
        except Exception as inner_e:
            raise ValidationError(
                "Invalid group metrics",
                context={"metrics": metrics_dict, "error": str(inner_e)},
            )
        self.logger.debug(
            "Aggregated group metrics",
            extra={
                "account_count": len(data),
                "total_trades": totals["trades"],
                "total_pnl": str(totals["pnl"]),
            },
        )
        return performance

    @error_handler(
        context_extractor=lambda self, data: {"data_length": sum(len(r) for r in data.values())},
        log_message="Cumulative metrics calculation failed"
    )
    async def aggregate_performance(
        self,
        data: Dict[str, List[PerformanceDict]]
    ) -> List[Dict[str, Any]]:
        """
        Aggregate daily performance into a cumulative timeseries.
        
        Returns:
            A list of dictionaries containing cumulative metrics.
        """
        if not data:
            raise ValidationError("No data for cumulative metrics", context={"data_length": 0})

        results = []
        running_pnl = Decimal("0")
        running_volume = Decimal("0")
        running_fees = Decimal("0")
        running_trades = 0
        running_wins = 0
        high_balance = None

        for account_id, records in data.items():
            for record in records:
                running_pnl += self._to_decimal(record.get("pnl", 0))
                running_volume += self._to_decimal(record.get("volume", 0))
                running_fees += (
                    self._to_decimal(record.get("trading_fees", 0))
                    + self._to_decimal(record.get("funding_fees", 0))
                )
                running_trades += record.get("trades", 0)
                running_wins += record.get("winning_trades", 0)

                current_balance = self._to_decimal(record.get("balance", 0))
                if high_balance is None or current_balance > high_balance:
                    high_balance = current_balance

                win_rate = (
                    round(running_wins / running_trades * 100, 2)
                    if running_trades > 0
                    else 0.0
                )
                roi = (
                    round(((current_balance - high_balance) / high_balance * 100), 2)
                    if high_balance and high_balance > 0
                    else 0.0
                )
                drawdown = (
                    round(((high_balance - current_balance) / high_balance * 100), 2)
                    if high_balance and high_balance > 0
                    else 0.0
                )

                cumulative = {
                    "date": record["date"],
                    "balance": float(current_balance),
                    "cumulative_pnl": float(running_pnl),
                    "cumulative_volume": float(running_volume),
                    "cumulative_fees": float(running_fees),
                    "total_trades": running_trades,
                    "win_rate": win_rate,
                    "roi": float(roi),
                    "drawdown": float(drawdown),
                }
                results.append(cumulative)

        self.logger.debug(
            "Calculated cumulative metrics",
            extra={
                "data_points": len(results),
                "final_pnl": str(running_pnl),
                "total_trades": running_trades,
            },
        )
        return results

    def _get_interval_timestamp(self, dt: datetime, interval: str) -> datetime:
        """
        Get normalized timestamp for the given interval.

        Args:
            dt: The datetime to normalize.
            interval: The interval type ("day", "week", "month", "quarter", "year").

        Returns:
            Normalized datetime representing the start of the interval.

        Raises:
            ValueError: If the interval is invalid.
        """
        dt = dt.astimezone(self.timezone)
        if interval == "day":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval == "week":
            return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval == "month":
            return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif interval == "quarter":
            quarter_start = ((dt.month - 1) // 3) * 3 + 1
            return dt.replace(month=quarter_start, day=1, hour=0, minute=0, second=0, microsecond=0)
        elif interval == "year":
            return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError(f"Invalid interval: {interval}")
