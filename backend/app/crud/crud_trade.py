from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.trade import Trade
from app.core.enums import TradeStatus
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

logger = get_logger(__name__)

class TradeCreate(BaseModel):
    """
    Schema for creating a new trade record.
    """
    user_id: str
    exchange_id: str
    exchange: str
    symbol: str
    side: str
    size: float
    entry_price: float
    status: TradeStatus
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("size", "entry_price")
    @classmethod
    def validate_positive_value(cls, v: float) -> float:
        if v <= 0:
            raise ValidationError("Value must be positive", context={"value": v})
        return v

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        side_lower = v.lower()
        if side_lower not in {"buy", "sell"}:
            raise ValidationError("Side must be 'buy' or 'sell'", context={"side": v})
        return side_lower

class TradeUpdate(BaseModel):
    """
    Schema for updating an existing trade.
    """
    close_price: Optional[float] = None
    pnl: Optional[float] = None
    fee: Optional[float] = None
    status: Optional[TradeStatus] = None

    @field_validator("close_price")
    @classmethod
    def validate_close_price(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValidationError("Close price must be positive", context={"close_price": v})
        return v

class CRUDTrade(CRUDBase[Trade, TradeCreate, TradeUpdate]):
    """
    CRUD operations for the Trade model with enhanced error handling.
    """

    @handle_db_error("Failed to retrieve trade by exchange ID", lambda self, exchange_id: {"exchange_id": exchange_id})
    async def get_by_exchange_id(self, exchange_id: str) -> Optional[Trade]:
        trade = await Trade.find_one({"exchange_id": exchange_id})
        if not trade:
            logger.debug(f"No trade found for exchange_id: {exchange_id}")
        return trade

    @handle_db_error("Failed to get user performance", lambda self, user_id, start_date, end_date: {"user_id": user_id, "date_range": f"{start_date} to {end_date}"})
    async def get_user_performance(
        self, user_id: str, start_date: datetime, end_date: datetime
    ) -> Dict:
        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "status": TradeStatus.CLOSED
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_trades": {"$sum": 1},
                    "winning_trades": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
                    "total_pnl": {"$sum": "$pnl"},
                    "total_fees": {"$sum": "$fee"},
                    "max_profit": {"$max": "$pnl"},
                    "max_loss": {"$min": "$pnl"}
                }
            }
        ]
        results = await Trade.aggregate(pipeline).to_list()
        if not results:
            logger.info(f"No trades found for user {user_id} in date range")
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "total_pnl": 0,
                "total_fees": 0,
                "win_rate": 0,
                "max_profit": 0,
                "max_loss": 0
            }
        metrics = results[0]
        metrics["win_rate"] = (metrics["winning_trades"] / metrics["total_trades"]) * 100
        logger.info(
            f"Retrieved performance metrics for user {user_id}",
            extra={
                "total_trades": metrics["total_trades"],
                "date_range": f"{start_date} to {end_date}"
            }
        )
        return metrics

    @handle_db_error("Failed to export trade history", lambda self, user_id, start_date, end_date: {"user_id": user_id, "date_range": f"{start_date} to {end_date}"})
    async def get_trade_history_export(
        self, user_id: str, start_date: datetime, end_date: datetime
    ) -> List[Dict]:
        trades = await Trade.find({
            "user_id": user_id,
            "created_at": {"$gte": start_date, "$lte": end_date}
        }).to_list()
        export_data = [
            {
                "date": trade.created_at,
                "exchange": trade.exchange,
                "symbol": trade.symbol,
                "side": trade.side,
                "size": trade.size,
                "entry_price": trade.entry_price,
                "close_price": trade.close_price,
                "pnl": trade.pnl or 0,
                "fee": trade.fee or 0,
                "net_pnl": (trade.pnl or 0) - (trade.fee or 0),
                "closed_at": trade.closed_at
            }
            for trade in trades
        ]
        logger.info(
            f"Exported trade history for user {user_id}",
            extra={
                "trade_count": len(export_data),
                "date_range": f"{start_date} to {end_date}"
            }
        )
        return export_data

    @handle_db_error("Failed to calculate daily performance", lambda self, user_id, start_date, end_date: {"user_id": user_id, "date_range": f"{start_date} to {end_date}"})
    async def calculate_daily_performance(
        self, user_id: str, start_date: datetime, end_date: datetime
    ) -> List[Dict]:
        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "status": TradeStatus.CLOSED
                }
            },
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                    },
                    "total_trades": {"$sum": 1},
                    "winning_trades": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
                    "total_pnl": {"$sum": "$pnl"},
                    "total_fees": {"$sum": "$fee"}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        results = await Trade.aggregate(pipeline).to_list()
        for result in results:
            result["date"] = result.pop("_id")
            result["win_rate"] = (result["winning_trades"] / result["total_trades"]) * 100
            result["net_pnl"] = result["total_pnl"] - result["total_fees"]
        logger.info(
            f"Calculated daily performance for user {user_id}",
            extra={
                "days_calculated": len(results),
                "date_range": f"{start_date} to {end_date}"
            }
        )
        return results

trade = CRUDTrade(Trade)
