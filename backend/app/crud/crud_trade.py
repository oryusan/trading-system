from datetime import datetime
from typing import List, Optional, Dict
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.trade import Trade
from app.models.common import TradeStatus
from app.core.errors import (
    DatabaseError,
    ValidationError,
    NotFoundError
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

class TradeCreate(BaseModel):
    """
    Schema for creating a new trade record.
    
    Attributes:
        user_id (str): The ID of the user initiating the trade.
        exchange_id (str): An identifier for the trade on the exchange's side.
        exchange (str): The name of the exchange (e.g., "Binance").
        symbol (str): The trading pair or symbol (e.g., "BTC/USD").
        side (str): The trade side ("buy" or "sell").
        size (float): The size of the position/trade.
        entry_price (float): The price at which the trade was entered.
        status (TradeStatus): The current status of the trade.
        created_at (datetime): The timestamp when the trade was created.
    """
    user_id: str
    exchange_id: str
    exchange: str
    symbol: str
    side: str
    size: float
    entry_price: float
    status: TradeStatus
    created_at: datetime = datetime.utcnow()

    @field_validator("size", "entry_price")
    @classmethod
    def validate_positive_value(cls, v: float) -> float:
        if v <= 0:
            raise ValidationError(
                "Value must be positive",
                context={"value": v}
            )
        return v

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        if v.lower() not in ["buy", "sell"]:
            raise ValidationError(
                "Side must be 'buy' or 'sell'",
                context={"side": v}
            )
        return v.lower()

class TradeUpdate(BaseModel):
    """
    Schema for updating an existing trade.
    
    Attributes:
        close_price (Optional[float]): The price at which the trade was closed.
        pnl (Optional[float]): The profit or loss from the trade.
        fee (Optional[float]): Any fees associated with the trade.
        status (Optional[TradeStatus]): Updated status of the trade.
    """
    close_price: Optional[float] = None
    pnl: Optional[float] = None
    fee: Optional[float] = None
    status: Optional[TradeStatus] = None

    @field_validator("close_price")
    @classmethod
    def validate_close_price(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValidationError(
                "Close price must be positive",
                context={"close_price": v}
            )
        return v

class CRUDTrade(CRUDBase[Trade, TradeCreate, TradeUpdate]):
    """
    CRUD operations for Trade model with enhanced validation.
    
    Features:
    - Trade state management
    - Performance tracking
    - Reference integrity
    - Enhanced error handling
    """

    async def get_by_exchange_id(self, exchange_id: str) -> Optional[Trade]:
        """
        Retrieve a trade by its exchange-specific ID.
        
        Args:
            exchange_id (str): The unique ID of the trade on the exchange's side.
            
        Returns:
            Optional[Trade]: The matching Trade document if found.
        
        Raises:
            DatabaseError: If database operation fails.
        """
        try:
            trade = await Trade.find_one({"exchange_id": exchange_id})
            if not trade:
                logger.debug(f"No trade found for exchange_id: {exchange_id}")
            return trade
        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve trade by exchange ID",
                context={
                    "exchange_id": exchange_id,
                    "error": str(e)
                }
            )

    async def get_user_performance(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """
        Retrieve aggregated performance metrics for a user over a given date range.
        
        The query filters for CLOSED trades within the given timeframe and calculates:
        - total_trades
        - winning_trades
        - total_pnl
        - total_fees
        - max_profit (highest PnL)
        - max_loss (lowest PnL)
        - win_rate (percentage of winning trades)
        
        Args:
            user_id (str): The ID of the user.
            start_date (datetime): Start of the date range.
            end_date (datetime): End of the date range.
        
        Returns:
            Dict: Performance metrics. If no trades, returns default zeroed metrics.
        
        Raises:
            DatabaseError: If aggregation fails.
        """
        try:
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
            
            result = await Trade.aggregate(pipeline).to_list()
            if not result:
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
                
            metrics = result[0]
            metrics["win_rate"] = (metrics["winning_trades"] / metrics["total_trades"]) * 100
            
            logger.info(
                f"Retrieved performance metrics for user {user_id}",
                extra={
                    "total_trades": metrics["total_trades"],
                    "date_range": f"{start_date} to {end_date}"
                }
            )
            
            return metrics
            
        except Exception as e:
            raise DatabaseError(
                "Failed to get user performance",
                context={
                    "user_id": user_id,
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def get_trade_history_export(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """
        Retrieve a formatted list of trades for export.
        
        For each trade, returns a dictionary with:
        - date
        - exchange
        - symbol
        - side
        - size
        - entry_price
        - close_price
        - pnl
        - fee
        - net_pnl (pnl - fee)
        
        Args:
            user_id (str): The user's ID for export.
            start_date (datetime): Start of date range.
            end_date (datetime): End of date range.
        
        Returns:
            List[Dict]: List of formatted trades for export.
        
        Raises:
            DatabaseError: If export fails.
        """
        try:
            trades = await Trade.find({
                "user_id": user_id,
                "created_at": {"$gte": start_date, "$lte": end_date}
            }).to_list()
            
            export_data = [{
                "date": trade.created_at,
                "exchange": trade.exchange,
                "symbol": trade.symbol,
                "side": trade.side,
                "size": trade.size,
                "entry_price": trade.entry_price,
                "close_price": trade.close_price,
                "pnl": trade.pnl or 0,
                "fee": trade.fee or 0,
                "net_pnl": ((trade.pnl or 0) - (trade.fee or 0))
            } for trade in trades]
            
            logger.info(
                f"Exported trade history for user {user_id}",
                extra={
                    "trade_count": len(export_data),
                    "date_range": f"{start_date} to {end_date}"
                }
            )
            
            return export_data
            
        except Exception as e:
            raise DatabaseError(
                "Failed to export trade history",
                context={
                    "user_id": user_id,
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def calculate_daily_performance(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """
        Calculate daily trading performance.
        
        Aggregates trades by date (YYYY-MM-DD) and calculates:
        - total_trades
        - winning_trades
        - total_pnl
        - total_fees
        - win_rate
        - net_pnl
        
        Args:
            user_id (str): The user's ID.
            start_date (datetime): Start of date range.
            end_date (datetime): End of date range.
        
        Returns:
            List[Dict]: Daily summaries with performance metrics.
        
        Raises:
            DatabaseError: If calculation fails.
        """
        try:
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
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$created_at"
                            }
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
            
        except Exception as e:
            raise DatabaseError(
                "Failed to calculate daily performance",
                context={
                    "user_id": user_id,
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

trade = CRUDTrade(Trade)