"""
Performance metric calculation implementation.

Features:
- Performance metric calculation
- Period-based metrics
- Risk metrics
- Win/loss statistics
"""

from typing import Dict, List, Any, Optional
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP, DecimalException

from app.core.errors.base import ValidationError
from app.core.logging.logger import get_logger
from app.core.references import (
    PerformanceMetrics,
    PerformanceDict,
    Numeric
)

logger = get_logger(__name__)

class PerformanceCalculator:
    """
    Calculates performance metrics from raw performance data.
    
    Features:
    - Balance based metrics
    - Trade statistics
    - Risk metrics
    - Period calculations
    """

    def __init__(self):
        """Initialize calculator with default precision."""
        self.logger = logger.getChild('performance_calculator')
        self.precision = 8

    async def calculate_metrics(
        self,
        balance: Decimal,
        equity: Decimal,
        metrics: PerformanceDict
    ) -> PerformanceMetrics:
        """
        Calculate performance metrics from current state.
        
        Args:
            balance: Current balance 
            equity: Current equity
            metrics: Additional metrics to include
            
        Returns:
            PerformanceMetrics containing calculated metrics
            
        Raises:
            ValidationError: If calculations fail
        """
        try:
            # Calculate basic metrics
            trades = metrics.get('trades', 0)
            winning_trades = metrics.get('winning_trades', 0)
            
            metrics_dict = {
                "start_balance": Decimal(str(metrics.get('start_balance', balance))),
                "end_balance": balance,
                "total_trades": trades,
                "winning_trades": winning_trades,
                "total_volume": Decimal(str(metrics.get('volume', 0))),
                "trading_fees": Decimal(str(metrics.get('trading_fees', 0))),
                "funding_fees": Decimal(str(metrics.get('funding_fees', 0))),
                "total_pnl": Decimal(str(metrics.get('pnl', 0)))
            }

            # Calculate ratios
            if trades > 0:
                metrics_dict["win_rate"] = round(winning_trades / trades * 100, 2)
            else:
                metrics_dict["win_rate"] = 0.0

            # Calculate ROI
            if metrics_dict["start_balance"] > 0:
                roi = ((balance - metrics_dict["start_balance"]) / 
                      metrics_dict["start_balance"] * 100)
                metrics_dict["roi"] = round(float(roi), 2)
            else:
                metrics_dict["roi"] = 0.0

            # Calculate drawdown
            max_equity = Decimal(str(metrics.get('max_equity', equity)))
            if max_equity > 0:
                drawdown = ((max_equity - equity) / max_equity * 100)
                metrics_dict["drawdown"] = round(float(drawdown), 2) 
            else:
                metrics_dict["drawdown"] = 0.0

            # Create PerformanceMetrics instance
            try:
                performance = PerformanceMetrics(**metrics_dict)
            except ValidationError as e:
                raise ValidationError(
                    "Invalid performance metrics",
                    context={
                        "metrics": metrics_dict,
                        "error": str(e)
                    }
                )

            self.logger.debug(
                "Calculated performance metrics",
                extra={
                    "balance": str(balance),
                    "equity": str(equity),
                    "trades": trades,
                    "win_rate": metrics_dict["win_rate"]
                }
            )

            return performance

        except DecimalException as e:
            raise ValidationError(
                "Performance calculation failed",
                context={
                    "balance": str(balance),
                    "equity": str(equity),
                    "error": str(e)
                }
            )

    async def calculate_period_metrics(
        self,
        data: List[PerformanceDict]
    ) -> PerformanceMetrics:
        """
        Calculate aggregated metrics for a time period.
        
        Args:
            data: List of daily performance records
            
        Returns:
            PerformanceMetrics containing aggregated metrics
            
        Raises:
            ValidationError: If calculations fail
        """
        try:
            if not data:
                raise ValidationError(
                    "No performance data provided",
                    context={"data_length": 0}
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

            # Track high/low values
            max_equity = Decimal(str(data[0].get('equity', 0)))
            start_balance = Decimal(str(data[0].get('balance', 0)))
            end_balance = Decimal(str(data[-1].get('balance', 0)))

            # Aggregate metrics
            for record in data:
                totals['trades'] += record.get('trades', 0)
                totals['winning_trades'] += record.get('winning_trades', 0)
                totals['volume'] += Decimal(str(record.get('volume', 0)))
                totals['trading_fees'] += Decimal(str(record.get('funding_fees', 0)))
                totals['funding_fees'] += Decimal(str(record.get('funding_fees', 0)))
                totals['pnl'] += Decimal(str(record.get('pnl', 0)))

                # Track max equity
                current_equity = Decimal(str(record.get('equity', 0)))
                max_equity = max(max_equity, current_equity)

            # Calculate period metrics
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

            # Calculate max drawdown
            if max_equity > 0:
                lowest_equity = min(Decimal(str(r.get('equity', 0))) for r in data)
                drawdown = ((max_equity - lowest_equity) / max_equity * 100)
                metrics_dict["drawdown"] = round(float(drawdown), 2)
            else:
                metrics_dict["drawdown"] = 0.0

            # Create PerformanceMetrics instance
            try:
                performance = PerformanceMetrics(**metrics_dict)
            except ValidationError as e:
                raise ValidationError(
                    "Invalid period metrics",
                    context={
                        "metrics": metrics_dict,
                        "error": str(e)
                    }
                )

            self.logger.debug(
                "Calculated period metrics",
                extra={
                    "start_balance": str(start_balance),
                    "end_balance": str(end_balance),
                    "trades": totals['trades'],
                    "pnl": str(totals['pnl'])
                }
            )

            return performance

        except DecimalException as e:
            raise ValidationError(
                "Period metrics calculation failed",
                context={
                    "data_length": len(data),
                    "error": str(e)
                }
            )

    async def calculate_risk_metrics(
        self,
        data: List[PerformanceDict]
    ) -> Dict[str, float]:
        """
        Calculate risk-related metrics.
        
        Args:
            data: List of daily performance records
            
        Returns:
            Dict containing:
            - max_drawdown: Maximum drawdown percentage 
            - volatility: Daily returns volatility
            - sharpe_ratio: Risk-adjusted returns
            - sortino_ratio: Downside risk-adjusted returns
            
        Raises:
            ValidationError: If calculations fail
        """
        try:
            if not data:
                raise ValidationError(
                    "No performance data provided", 
                    context={"data_length": 0}
                )

            # Calculate daily returns
            returns = []
            for i in range(1, len(data)):
                prev_equity = Decimal(str(data[i-1].get('equity', 0)))
                curr_equity = Decimal(str(data[i].get('equity', 0)))
                
                if prev_equity > 0:
                    daily_return = ((curr_equity - prev_equity) / prev_equity) * 100
                    returns.append(float(daily_return))

            if not returns:
                return {
                    "max_drawdown": 0.0,
                    "volatility": 0.0,
                    "sharpe_ratio": 0.0,
                    "sortino_ratio": 0.0
                }

            # Calculate metrics
            max_drawdown = self._calculate_max_drawdown(data)
            volatility = self._calculate_volatility(returns)
            sharpe = self._calculate_sharpe_ratio(returns, volatility)
            sortino = self._calculate_sortino_ratio(returns)

            metrics = {
                "max_drawdown": round(max_drawdown, 2),
                "volatility": round(volatility, 2),
                "sharpe_ratio": round(sharpe, 2),
                "sortino_ratio": round(sortino, 2)
            }

            self.logger.debug(
                "Calculated risk metrics",
                extra={
                    "data_points": len(data),
                    "max_drawdown": metrics["max_drawdown"],
                    "volatility": metrics["volatility"]
                }
            )

            return metrics

        except Exception as e:
            raise ValidationError(
                "Risk metrics calculation failed",
                context={
                    "data_length": len(data),
                    "error": str(e)
                }
            )

    def _calculate_max_drawdown(self, data: List[PerformanceDict]) -> float:
        """Calculate maximum drawdown percentage."""
        peak = Decimal('0')
        max_drawdown = Decimal('0')

        for record in data:
            equity = Decimal(str(record.get('equity', 0)))
            if equity > peak:
                peak = equity
            elif peak > 0:
                drawdown = ((peak - equity) / peak) * 100
                max_drawdown = max(max_drawdown, drawdown)

        return float(max_drawdown)

    def _calculate_volatility(self, returns: List[float]) -> float:
        """Calculate daily returns volatility (standard deviation)."""
        if not returns:
            return 0.0

        mean = sum(returns) / len(returns)
        squared_diff_sum = sum((r - mean) ** 2 for r in returns)
        variance = squared_diff_sum / len(returns)
        return (variance ** 0.5)

    def _calculate_sharpe_ratio(
        self,
        returns: List[float],
        volatility: float,
        risk_free_rate: float = 0.02  # 2% annual
    ) -> float:
        """Calculate Sharpe ratio (risk-adjusted returns)."""
        if not returns or volatility == 0:
            return 0.0

        # Convert annual risk-free rate to daily
        daily_rf = (1 + risk_free_rate) ** (1/252) - 1
        
        avg_return = sum(returns) / len(returns)
        excess_return = avg_return - daily_rf
        
        return (excess_return / volatility) * (252 ** 0.5)  # Annualized

    def _calculate_sortino_ratio(
        self,
        returns: List[float],
        risk_free_rate: float = 0.02  # 2% annual
    ) -> float:
        """Calculate Sortino ratio (downside risk-adjusted returns)."""
        if not returns:
            return 0.0

        # Convert annual risk-free rate to daily
        daily_rf = (1 + risk_free_rate) ** (1/252) - 1
        
        avg_return = sum(returns) / len(returns)
        excess_return = avg_return - daily_rf
        
        # Calculate downside deviation
        negative_returns = [r for r in returns if r < 0]
        if not negative_returns:
            return 0.0
            
        squared_downside = sum(r ** 2 for r in negative_returns)
        downside_deviation = (squared_downside / len(returns)) ** 0.5
        
        if downside_deviation == 0:
            return 0.0
            
        return (excess_return / downside_deviation) * (252 ** 0.5)  # Annualized