"""
Performance Analytics Service

This module provides functionality to calculate and report performance metrics 
from raw trade data stored in MongoDB. It consolidates analytics previously performed 
in a separate module and uses MongoDB aggregation pipelines (and, optionally, Pandas) 
to process trade data.

Public methods are decorated with a global error‑handling decorator so that any 
exceptions are uniformly caught and re‑raised with additional context.
"""

from datetime import datetime, timedelta
from typing import Dict, List
from decimal import Decimal
from motor.motor_asyncio import AsyncIOMotorClient
import pandas as pd

from app.core.errors.base import ValidationError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class PerformanceAnalyticsService:
    """
    Performance Analytics Service

    This service calculates and reports performance metrics based on raw trade data 
    stored in MongoDB using aggregation pipelines.
    """

    def __init__(self, db: AsyncIOMotorClient):
        self.db = db

    @error_handler(
        context_extractor=lambda self, user_id, start_date, end_date: {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        log_message="Failed to aggregate performance metrics"
    )
    async def get_performance_metrics(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """
        Calculate comprehensive performance metrics for the given user and time range.
        
        Uses a MongoDB aggregation pipeline to group trades by day and compute totals
        (e.g. total trades, winning trades, gross profit/loss, fees, net PnL, etc.).
        """
        collection = self.db["trades"]
        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "status": "closed"
                }
            },
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at"
                        }
                    },
                    "total_trades": {"$sum": 1},
                    "winning_trades": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
                    "gross_profit": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}},
                    "gross_loss": {"$sum": {"$cond": [{"$lt": ["$pnl", 0]}, "$pnl", 0]}},
                    "total_pnl": {"$sum": "$pnl"},
                    "total_fees": {"$sum": "$fee"},
                    "total_volume": {"$sum": {"$multiply": ["$size", "$entry_price"]}},
                    "max_profit": {"$max": "$pnl"},
                    "max_loss": {"$min": "$pnl"},
                    "avg_trade_duration": {
                        "$avg": {
                            "$divide": [
                                {"$subtract": ["$closed_at", "$created_at"]},
                                3600000
                            ]
                        }
                    }
                }
            },
            {
                "$addFields": {
                    "win_rate": {
                        "$multiply": [
                            {"$divide": ["$winning_trades", "$total_trades"]},
                            100
                        ]
                    },
                    "profit_factor": {
                        "$cond": [
                            {"$eq": ["$gross_loss", 0]},
                            None,
                            {"$abs": {"$divide": ["$gross_profit", "$gross_loss"]}}
                        ]
                    },
                    "net_pnl": {"$subtract": ["$total_pnl", "$total_fees"]},
                    "avg_trade_value": {"$divide": ["$net_pnl", "$total_trades"]}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        results = await collection.aggregate(pipeline).to_list(None)
        return results

    @error_handler(
        context_extractor=lambda self, user_id, start_date, end_date: {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        log_message="Failed to aggregate symbol performance"
    )
    async def get_symbol_performance(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """
        Calculate performance metrics broken down by symbol.

        Uses a MongoDB aggregation pipeline to group trades by symbol and compute metrics.
        """
        collection = self.db["trades"]
        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "status": "closed"
                }
            },
            {
                "$group": {
                    "_id": "$symbol",
                    "total_trades": {"$sum": 1},
                    "winning_trades": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
                    "total_pnl": {"$sum": "$pnl"},
                    "total_fees": {"$sum": "$fee"},
                    "total_volume": {"$sum": {"$multiply": ["$size", "$entry_price"]}},
                    "avg_leverage": {"$avg": "$leverage"}
                }
            },
            {
                "$project": {
                    "symbol": "$_id",
                    "total_trades": 1,
                    "win_rate": {
                        "$multiply": [
                            {"$divide": ["$winning_trades", "$total_trades"]},
                            100
                        ]
                    },
                    "net_pnl": {"$subtract": ["$total_pnl", "$total_fees"]},
                    "total_volume": 1,
                    "avg_leverage": 1
                }
            },
            {"$sort": {"net_pnl": -1}}
        ]
        results = await collection.aggregate(pipeline).to_list(None)
        return results

    @error_handler(
        context_extractor=lambda self, user_id, start_date, end_date: {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        log_message="Failed to perform drawdown analysis"
    )
    async def get_drawdown_analysis(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """
        Calculate drawdown metrics based on a user's closed trades.

        Retrieves trade data via a separate CRUD method, converts the data to a Pandas DataFrame,
        computes cumulative PnL, rolling maximum, and derives drawdown metrics.
        """
        from app.crud.crud_Trade import trade
        trades = await trade_crud.get_user_trades(self.db, user_id, start_date, end_date)
        if not trades:
            return {"max_drawdown": 0, "max_drawdown_duration": 0, "current_drawdown": 0}
        
        df = pd.DataFrame(trades)
        if "pnl" not in df or "created_at" not in df:
            raise ValidationError("Missing required fields in trade data", context={"fields": list(df.columns)})
        
        df['cumulative_pnl'] = df['pnl'].cumsum()
        df['rolling_max'] = df['cumulative_pnl'].cummax()
        df['drawdown'] = df['rolling_max'] - df['cumulative_pnl']
        
        max_drawdown = df['drawdown'].max()
        current_drawdown = df['drawdown'].iloc[-1]
        
        drawdown_start = None
        max_duration = 0
        for _, row in df.iterrows():
            if row['drawdown'] > 0 and drawdown_start is None:
                drawdown_start = row['created_at']
            elif row['drawdown'] == 0 and drawdown_start is not None:
                duration = (row['created_at'] - drawdown_start).total_seconds() / 3600
                max_duration = max(max_duration, duration)
                drawdown_start = None
        current_duration = ((df['created_at'].iloc[-1] - drawdown_start).total_seconds() / 3600
                            if drawdown_start is not None else 0)

        return {
            "max_drawdown": max_drawdown,
            "max_drawdown_duration": max_duration,
            "current_drawdown": current_drawdown
        }
