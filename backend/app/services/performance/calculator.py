"""
Performance metric calculation implementation.

This module provides functionality to calculate aggregated performance metrics from daily
performance records. It now assumes that the underlying data are derived exclusively from
closed (realized) trades rather than from a snapshot of open positions.

Expected keys in the metrics dictionary:
  - closed_trades: Count of finalized (closed) trades for the day.
  - closed_trade_value: Total notional value of those closed trades.
  - trading_fees: Total trading fees incurred.
  - funding_fees: Total funding fees incurred.
  - total_pnl: The gross profit/loss from all closed trades.
"""

from typing import Dict, Any
from decimal import Decimal, InvalidOperation
from app.core.errors.base import ValidationError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class PerformanceCalculator:
    """
    Calculates performance metrics from daily performance data records.

    This implementation now expects that the provided performance metrics are based on closed (realized)
    trades. For example, instead of a generic "positions" count, it expects a "closed_trades" count, and
    instead of "position_value", it expects "closed_trade_value". Additional fields such as "trading_fees",
    "funding_fees", and "total_pnl" (gross profit/loss) may also be provided.
    """
    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.precision = 8  # reserved for future rounding if needed

    @error_handler(
        context_extractor=lambda self, balance, equity, metrics: {
            "balance": str(balance),
            "equity": str(equity),
            "metrics_keys": list(metrics.keys())
        },
        log_message="Failed to calculate performance metrics"
    )
    async def calculate_metrics(
        self, 
        balance: Decimal, 
        equity: Decimal, 
        metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate aggregated performance metrics based on closed-trade data.

        Args:
            balance (Decimal): The account balance.
            equity (Decimal): The account equity.
            metrics (Dict[str, Any]): A dictionary of performance metrics expected to include:
                - closed_trades: Count of finalized (closed) trades for the day.
                - closed_trade_value: Total notional value of those closed trades.
                (Other fields such as realized PnL, fees, etc.)
        
        Returns:
            A dictionary containing:
                - balance, equity,
                - closed_trades, closed_trade_value,
                - trading_fees, funding_fees,
                - total_pnl (gross),
                - net_pnl (total_pnl minus fees),
                - avg_trade_value (average notional per trade),
                - roi (net PnL as a percentage of balance).
        
        Raises:
            ValidationError: If metric calculation fails due to invalid inputs.
        """
        closed_trades = int(metrics.get("closed_trades", 0))
        closed_trade_value = Decimal(str(metrics.get("closed_trade_value", "0")))
        trading_fees = Decimal(str(metrics.get("trading_fees", "0")))
        funding_fees = Decimal(str(metrics.get("funding_fees", "0")))
        total_pnl = Decimal(str(metrics.get("total_pnl", "0")))
            
        # Calculate net PnL (realized PnL after deducting fees)
        net_pnl = total_pnl - trading_fees - funding_fees
            
        # Calculate average trade value, if there are any closed trades
        avg_trade_value = (
            closed_trade_value / Decimal(closed_trades)
            if closed_trades > 0
            else Decimal("0")
        )
            
        # Calculate ROI as a percentage of the balance
        roi = (net_pnl / balance * Decimal("100")) if balance > 0 else Decimal("0")
            
        calculated = {
            "balance": balance,
            "equity": equity,
            "closed_trades": closed_trades,
            "closed_trade_value": closed_trade_value,
            "trading_fees": trading_fees,
            "funding_fees": funding_fees,
            "total_pnl": total_pnl,
            "net_pnl": net_pnl,
            "avg_trade_value": avg_trade_value,
            "roi": roi,
        }
        self.logger.debug("Calculated performance metrics", extra=calculated)
        return calculated
