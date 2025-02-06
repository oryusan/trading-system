# app/services/analytics.py
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from pandas import DataFrame, date_range
import pandas as pd
from app.crud.crud_trade import trade_crud

class TradeAnalytics:
    def __init__(self, db: AsyncIOMotorClient):
        self.db = db

    async def get_performance_metrics(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime,
        group_by: str = "day"  # 'day', 'week', 'month', 'year'
    ) -> Dict:
        """Calculate comprehensive performance metrics"""
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
                        } if group_by == "day" else 
                        {"$week": "$created_at"} if group_by == "week" else
                        {"$month": "$created_at"} if group_by == "month" else
                        {"$year": "$created_at"}
                    },
                    "total_trades": {"$sum": 1},
                    "winning_trades": {
                        "$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}
                    },
                    "losing_trades": {
                        "$sum": {"$cond": [{"$lt": ["$pnl", 0]}, 1, 0]}
                    },
                    "gross_profit": {
                        "$sum": {"$cond": [{"$gt": ["$pnl", 0]}, "$pnl", 0]}
                    },
                    "gross_loss": {
                        "$sum": {"$cond": [{"$lt": ["$pnl", 0]}, "$pnl", 0]}
                    },
                    "total_pnl": {"$sum": "$pnl"},
                    "total_fees": {"$sum": "$fee"},
                    "total_volume": {"$sum": {"$multiply": ["$size", "$entry_price"]}},
                    "max_profit": {"$max": "$pnl"},
                    "max_loss": {"$min": "$pnl"},
                    "avg_trade_duration": {
                        "$avg": {
                            "$divide": [
                                {"$subtract": ["$closed_at", "$created_at"]},
                                3600000  # Convert to hours
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
                            null,
                            {"$abs": {"$divide": ["$gross_profit", "$gross_loss"]}}
                        ]
                    },
                    "net_pnl": {"$subtract": ["$total_pnl", "$total_fees"]},
                    "avg_profit_per_trade": {
                        "$divide": ["$net_pnl", "$total_trades"]
                    }
                }
            },
            {"$sort": {"_id": 1}}
        ]

        results = await collection.aggregate(pipeline).to_list(None)
        return results

    async def get_symbol_performance(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """Analyze performance by symbol"""
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
                    "winning_trades": {
                        "$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}
                    },
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

        return await collection.aggregate(pipeline).to_list(None)

    async def get_drawdown_analysis(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """Calculate drawdown metrics"""
        trades = await trade_crud.get_user_trades(
            self.db, user_id, start_date, end_date
        )
        
        if not trades:
            return {
                "max_drawdown": 0,
                "max_drawdown_duration": 0,
                "current_drawdown": 0
            }

        # Convert to DataFrame for easier analysis
        df = DataFrame(trades)
        df['cumulative_pnl'] = df['pnl'].cumsum()
        df['rolling_max'] = df['cumulative_pnl'].cummax()
        df['drawdown'] = df['rolling_max'] - df['cumulative_pnl']
        
        max_drawdown = df['drawdown'].max()
        current_drawdown = df['drawdown'].iloc[-1]
        
        # Calculate drawdown duration
        drawdown_start = None
        max_duration = timedelta(0)
        current_duration = timedelta(0)
        
        for i, row in df.iterrows():
            if row['drawdown'] > 0 and drawdown_start is None:
                drawdown_start = row['created_at']
            elif row['drawdown'] == 0:
                if drawdown_start:
                    duration = row['created_at'] - drawdown_start
                    max_duration = max(max_duration, duration)
                drawdown_start = None

        if drawdown_start:
            current_duration = df['created_at'].iloc[-1] - drawdown_start

        return {
            "max_drawdown": max_drawdown,
            "max_drawdown_duration": max_duration.total_seconds() / 3600,  # in hours
            "current_drawdown": current_drawdown
        }
