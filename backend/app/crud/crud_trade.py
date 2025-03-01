"""
Trade CRUD operations with centralized service integration.

This module centralizes all service integration for trade operations:
- Reference validation and management 
- Performance tracking
- Trade lifecycle management
- Export functionality
"""

from decimal import Decimal, DecimalException
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union, Type, TypeVar
from beanie import PydanticObjectId

from pydantic import BaseModel, Field, field_validator, model_validator

from app.crud.crud_base import CRUDBase, ModelType
from app.models.entities.trade import Trade
from app.core.references import TradeStatus, OrderType, TradeSource, PositionSide
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, ExchangeError
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

# Import service dependencies
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)


class TradeCreate(BaseModel):
    """
    Schema for creating a new trade record with comprehensive validation.
    """
    account_id: str = Field(..., description="Account executing trade")
    bot_id: Optional[str] = Field(None, description="Bot initiating trade")
    symbol: str = Field(..., description="Trading symbol")
    order_type: OrderType = Field(..., description="Type of trade order")
    side: PositionSide = Field(..., description="Trading side (buy/sell)")
    size: Decimal = Field(..., description="Trade size in units (quantity of the asset)")
    entry_price: Optional[Decimal] = Field(None, description="Execution entry price")
    risk_percentage: Decimal = Field(..., description="Risk percentage relative to balance")
    leverage: int = Field(..., description="Position leverage")
    order_size: Decimal = Field(..., description="Order size in USD value (size * entry_price)")
    take_profit: Optional[Decimal] = Field(None, description="Take profit price")
    stop_loss: Optional[Decimal] = Field(None, description="Stop loss price")
    status: TradeStatus = Field(TradeStatus.PENDING, description="Current trade status")
    source: TradeSource = Field(..., description="Trade signal source")
    exchange_order_id: Optional[str] = Field(None, description="Exchange order ID")
    
    @field_validator("size", "order_size", "risk_percentage")
    @classmethod
    def validate_positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValidationError("Value must be positive", context={"value": str(v)})
        return v
    
    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: int) -> int:
        from app.core.config.constants import trading_constants
        if not (trading_constants.MIN_LEVERAGE <= v <= trading_constants.MAX_LEVERAGE):
            raise ValidationError(
                f"Leverage must be between {trading_constants.MIN_LEVERAGE} and {trading_constants.MAX_LEVERAGE}",
                context={"leverage": v}
            )
        return v
    
    @field_validator("risk_percentage")
    @classmethod
    def validate_risk_percentage(cls, v: Decimal) -> Decimal:
        from app.core.config.constants import trading_constants
        min_risk = Decimal(str(trading_constants.MIN_RISK_PERCENTAGE))
        max_risk = Decimal(str(trading_constants.MAX_RISK_PERCENTAGE))
        if not (min_risk <= v <= max_risk):
            raise ValidationError(
                f"Risk percentage must be between {trading_constants.MIN_RISK_PERCENTAGE} and {trading_constants.MAX_RISK_PERCENTAGE}",
                context={"risk_percentage": str(v)}
            )
        return v


class TradeUpdate(BaseModel):
    """
    Schema for updating an existing trade with validation.
    """
    status: Optional[TradeStatus] = None
    exit_price: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    trading_fees: Optional[Decimal] = None
    funding_fees: Optional[Decimal] = None
    exchange_order_id: Optional[str] = None
    exchange_status: Optional[str] = None
    last_error: Optional[str] = None
    
    @field_validator("exit_price", "take_profit", "stop_loss")
    @classmethod
    def validate_prices(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValidationError("Price must be positive", context={"price": str(v)})
        return v


class TradeSummary(BaseModel):
    """
    Summary model for trade aggregation responses.
    """
    total_trades: int
    winning_trades: int
    total_pnl: float
    total_fees: float
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    by_symbol: Dict[str, Dict[str, Any]]


class CRUDTrade(CRUDBase[Trade, TradeCreate, TradeUpdate]):
    """
    Comprehensive CRUD operations for the Trade model with centralized service integration.
    
    This class centralizes all service calls and provides:
    - Complete trade lifecycle management
    - Performance calculations
    - Trade history and exports 
    - Reference validation and management
    """

    # -------------------------------------------------------------
    # REFERENCE VALIDATION AND SERVICE INTEGRATION
    # -------------------------------------------------------------
    
    @handle_db_error("Failed to validate references", lambda self, account_id, bot_id=None: {"account_id": account_id, "bot_id": bot_id})
    async def validate_references(self, account_id: str, bot_id: Optional[str] = None) -> None:
        """
        Centralized validation for all trade references.
        
        This method replaces the model's @before_event hook to provide
        centralized service integration.
        
        Args:
            account_id: The account identifier
            bot_id: Optional bot identifier
            
        Raises:
            NotFoundError: If a referenced entity doesn't exist
            ValidationError: If validation fails
        """
        # Validate account reference
        valid_account = await reference_manager.validate_reference(
            source_type="Trade",
            target_type="Account",
            reference_id=account_id
        )
        if not valid_account:
            raise NotFoundError(
                "Referenced account not found",
                context={"account_id": account_id}
            )
        
        # Validate bot reference if provided
        if bot_id:
            valid_bot = await reference_manager.validate_reference(
                source_type="Trade",
                target_type="Bot",
                reference_id=bot_id
            )
            if not valid_bot:
                raise NotFoundError(
                    "Referenced bot not found",
                    context={"bot_id": bot_id}
                )
    
    @handle_db_error("Failed to update performance metrics", lambda self, account_id, trade_id, metrics: {"account_id": account_id, "trade_id": trade_id})
    async def update_performance_metrics(
        self, 
        account_id: str, 
        trade_id: str,
        metrics: Dict[str, Any]
    ) -> None:
        """
        Centralized method to update performance metrics for a trade.
        
        Args:
            account_id: The account identifier
            trade_id: The trade identifier
            metrics: Performance metrics to record
        """
        await performance_service.record_trade_created(
            account_id=account_id,
            trade_id=trade_id,
            metrics=metrics
        )
    
    @handle_db_error("Failed to get exchange order status", lambda self, account_id, order_id: {"account_id": account_id, "order_id": order_id})
    async def get_exchange_order_status(self, account_id: str, order_id: str) -> Dict[str, Any]:
        """
        Centralized method to get exchange order status.
        
        Args:
            account_id: The account identifier
            order_id: The exchange order ID
            
        Returns:
            Order status information
        """
        # This would integrate with exchange_factory or a similar service
        # For now, it's a placeholder that returns a dummy status
        return {"status": "FILLED", "filled_qty": "0", "avg_price": "0"}

    # -------------------------------------------------------------
    # TRADE LIFECYCLE MANAGEMENT
    # -------------------------------------------------------------

    @handle_db_error("Failed to create trade", lambda self, obj_in: {"account_id": obj_in.account_id, "symbol": obj_in.symbol})
    async def create_trade(self, trade_data: TradeCreate) -> Trade:
        """
        Create a new trade with proper validation and reference checks.
        
        This is a higher-level method that handles validation beyond basic create.
        """
        # Validate account and bot references - centralized service integration
        await self.validate_references(trade_data.account_id, trade_data.bot_id)
        
        # Create trade using base method
        trade = await self.create(trade_data)
        
        # Update performance tracking - centralized service integration
        try:
            await self.update_performance_metrics(
                account_id=trade_data.account_id,
                trade_id=str(trade.id),
                metrics={
                    "order_size": float(trade_data.order_size),
                    "risk_percentage": float(trade_data.risk_percentage),
                    "leverage": trade_data.leverage,
                    "symbol": trade_data.symbol
                }
            )
        except Exception as e:
            logger.warning(f"Failed to update performance metrics: {str(e)}")
        
        logger.info(
            "Created new trade",
            extra={
                "trade_id": str(trade.id),
                "account_id": trade_data.account_id,
                "symbol": trade_data.symbol,
                "side": trade_data.side.value
            }
        )
        
        return trade
    
    @handle_db_error("Failed to close trade", lambda self, trade_id, exit_data: {"trade_id": str(trade_id)})
    async def close_trade(
        self,
        trade_id: PydanticObjectId,
        exit_data: Dict[str, Any]
    ) -> Trade:
        """
        Close a trade with proper P&L calculation and performance tracking.
        
        Args:
            trade_id: ID of trade to close
            exit_data: Dict containing exit_price, trading_fees, and funding_fees
            
        Returns:
            Updated Trade object
        """
        # Get trade
        trade = await self.get(trade_id)
        
        # Validate trade can be closed
        trade._validate_close_conditions()
        
        # Extract and validate required fields
        try:
            exit_price = Decimal(str(exit_data["exit_price"]))
            trading_fees = Decimal(str(exit_data.get("trading_fees", 0)))
            funding_fees = Decimal(str(exit_data.get("funding_fees", 0)))
        except (KeyError, ValueError, DecimalException) as e:
            raise ValidationError(
                "Invalid exit data",
                context={"exit_data": exit_data, "error": str(e)}
            )
        
        # Update trade state
        trade._update_trade_state(
            exit_price=exit_price,
            trading_fees=trading_fees,
            funding_fees=funding_fees
        )
        
        # Calculate P&L
        trade._calculate_pnl()
        
        # Update modified timestamp
        trade.modified_at = datetime.utcnow()
        
        # Save changes
        await trade.save()
        
        # Update performance metrics - centralized service integration
        await performance_service.record_trade_closed(
            account_id=trade.account_id,
            trade_id=str(trade.id),
            metrics={
                "pnl": float(trade.pnl if trade.pnl else 0),
                "pnl_percentage": trade.pnl_percentage,
                "trading_fees": float(trading_fees),
                "funding_fees": float(funding_fees),
                "exit_price": float(exit_price),
                "holding_time": (trade.closed_at - trade.executed_at).total_seconds() / 3600
            }
        )
        
        logger.info(
            "Closed trade",
            extra={
                "trade_id": str(trade_id),
                "exit_price": str(exit_price),
                "pnl": str(trade.pnl)
            }
        )
        
        return trade
    
    @handle_db_error("Failed to update trade status", lambda self, trade_id, status, error=None: {"trade_id": str(trade_id), "status": status})
    async def update_trade_status(
        self,
        trade_id: PydanticObjectId,
        status: TradeStatus,
        error: Optional[str] = None
    ) -> Trade:
        """
        Update a trade's status with proper validation and tracking.
        """
        trade = await self.get(trade_id)
        
        # Validate status transition
        valid_transitions = {
            TradeStatus.PENDING: [TradeStatus.OPEN, TradeStatus.CANCELLED, TradeStatus.ERROR],
            TradeStatus.OPEN: [TradeStatus.CLOSED, TradeStatus.ERROR],
            TradeStatus.CLOSED: [TradeStatus.ERROR],  # Only allow transition to ERROR
            TradeStatus.CANCELLED: [TradeStatus.ERROR],  # Only allow transition to ERROR
            TradeStatus.ERROR: []  # No valid transitions from ERROR
        }
        
        if status not in valid_transitions.get(trade.status, []):
            raise ValidationError(
                "Invalid status transition",
                context={
                    "trade_id": str(trade_id),
                    "current_status": trade.status.value,
                    "requested_status": status.value
                }
            )
        
        # Update the trade
        trade.status = status
        if error:
            trade.last_error = error
        trade.modified_at = datetime.utcnow()
        await trade.save()
        
        # Update performance metrics if status changed to OPEN
        if status == TradeStatus.OPEN:
            await performance_service.update_open_position_count(
                account_id=trade.account_id,
                count_change=1
            )
        
        logger.info(
            "Updated trade status",
            extra={
                "trade_id": str(trade_id),
                "previous_status": trade.status.value,
                "new_status": status.value
            }
        )
        
        return trade
    
    @handle_db_error("Failed to update exchange status", lambda self, trade_id, exchange_status, error=None: {"trade_id": str(trade_id), "exchange_status": exchange_status})
    async def update_exchange_status(
        self,
        trade_id: PydanticObjectId,
        exchange_status: str,
        error: Optional[str] = None
    ) -> Trade:
        """
        Update a trade's exchange status information.
        """
        trade = await self.get(trade_id)
        trade.exchange_status = exchange_status
        trade.last_error = error
        trade.modified_at = datetime.utcnow()
        await trade.save()
        
        logger.info(
            "Updated exchange status",
            extra={
                "trade_id": str(trade_id),
                "exchange_status": exchange_status
            }
        )
        
        return trade
    
    @handle_db_error("Failed to cancel trade", lambda self, trade_id, reason=None: {"trade_id": str(trade_id)})
    async def cancel_trade(
        self,
        trade_id: PydanticObjectId,
        reason: Optional[str] = None
    ) -> Trade:
        """
        Cancel a pending trade.
        """
        trade = await self.get(trade_id)
        
        # Validate trade can be cancelled
        if trade.status != TradeStatus.PENDING:
            raise ValidationError(
                "Only pending trades can be cancelled",
                context={"trade_id": str(trade_id), "status": trade.status.value}
            )
        
        # Update trade
        trade.status = TradeStatus.CANCELLED
        trade.last_error = reason
        trade.modified_at = datetime.utcnow()
        await trade.save()
        
        logger.info(
            "Cancelled trade",
            extra={
                "trade_id": str(trade_id),
                "reason": reason
            }
        )
        
        return trade

    # -------------------------------------------------------------
    # ACCOUNT-CENTRIC OPERATIONS
    # -------------------------------------------------------------
    
    @handle_db_error("Failed to get account trades", lambda self, account_id, **kwargs: {"account_id": account_id})
    async def get_account_trades(
        self,
        account_id: str,
        status: Optional[TradeStatus] = None,
        symbol: Optional[str] = None,
        limit: int = 100,
        skip: int = 0,
        sort_desc: bool = True
    ) -> List[Trade]:
        """
        Get trades for an account with filtering, pagination and sorting.
        """
        # Build query
        query = {"account_id": account_id}
        if status:
            query["status"] = status
        if symbol:
            query["symbol"] = symbol.upper()
        
        # Execute query with sort
        sort_field = [("executed_at", -1 if sort_desc else 1)]
        trades = await self.model.find(query).sort(sort_field).skip(skip).limit(limit).to_list()
        
        logger.info(
            "Retrieved account trades",
            extra={
                "account_id": account_id,
                "trade_count": len(trades),
                "filters": {"status": status.value if status else None, "symbol": symbol}
            }
        )
        
        return trades
    
    @handle_db_error("Failed to get open positions", lambda self, account_id, symbol=None: {"account_id": account_id, "symbol": symbol})
    async def get_open_positions(
        self,
        account_id: str,
        symbol: Optional[str] = None
    ) -> List[Trade]:
        """
        Get all open positions for an account, optionally filtered by symbol.
        """
        query = {"account_id": account_id, "status": TradeStatus.OPEN}
        if symbol:
            query["symbol"] = symbol.upper()
        
        positions = await self.model.find(query).to_list()
        
        logger.info(
            "Retrieved open positions",
            extra={
                "account_id": account_id,
                "symbol": symbol,
                "position_count": len(positions)
            }
        )
        
        return positions
    
    @handle_db_error("Failed to close all positions", lambda self, account_id: {"account_id": account_id})
    async def close_all_positions(
        self,
        account_id: str,
        close_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Close all open positions for an account with the provided close data.
        
        Args:
            account_id: Account ID
            close_data: Dict mapping symbol to close data (exit_price, fees)
            
        Returns:
            Summary of closed positions
        """
        # Get open positions
        positions = await self.get_open_positions(account_id)
        
        if not positions:
            return {"closed_count": 0, "success": True, "positions": []}
        
        # Close each position
        results = []
        for position in positions:
            symbol = position.symbol
            try:
                # Get close data for this symbol
                if symbol not in close_data:
                    logger.warning(f"No close data for symbol {symbol}, using market price")
                    # Handle missing close data (could integrate with exchange service here)
                    # For now we'll skip this position
                    continue
                
                # Close the position
                position_data = close_data[symbol]
                closed_position = await self.close_trade(position.id, position_data)
                results.append({
                    "trade_id": str(closed_position.id),
                    "symbol": symbol,
                    "success": True,
                    "pnl": str(closed_position.pnl) if closed_position.pnl else None
                })
            except Exception as e:
                logger.error(
                    f"Failed to close position {symbol}",
                    extra={"trade_id": str(position.id), "error": str(e)}
                )
                results.append({
                    "trade_id": str(position.id),
                    "symbol": symbol,
                    "success": False,
                    "error": str(e)
                })
        
        # Calculate summary
        closed_count = sum(1 for r in results if r["success"])
        total_count = len(positions)
        
        # Update performance metrics
        await performance_service.update_open_position_count(
            account_id=account_id,
            count_change=-closed_count  # Negative because we're reducing positions
        )
        
        logger.info(
            "Closed all positions",
            extra={
                "account_id": account_id,
                "closed_count": closed_count,
                "total_count": total_count
            }
        )
        
        return {
            "closed_count": closed_count,
            "total_count": total_count,
            "success": closed_count == total_count,
            "positions": results
        }

    # -------------------------------------------------------------
    # PERFORMANCE AND METRICS
    # -------------------------------------------------------------
    
    @handle_db_error("Failed to get account performance", lambda self, account_id, start_date, end_date: {"account_id": account_id, "date_range": f"{start_date} to {end_date}"})
    async def get_account_performance(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get comprehensive account performance metrics.
        """
        # Get closed trades in the period
        closed_trades = await self.model.find({
            "account_id": account_id,
            "status": TradeStatus.CLOSED,
            "closed_at": {"$gte": start_date, "$lte": end_date}
        }).to_list()
        
        # Calculate metrics
        total_trades = len(closed_trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "trading_fees": 0,
                "funding_fees": 0,
                "net_pnl": 0,
                "avg_trade_size": 0,
                "avg_holding_time": 0,
                "profit_factor": 0,
                "by_symbol": {}
            }
        
        # Aggregation calculations
        winning_trades = sum(1 for t in closed_trades if t.pnl and t.pnl > 0)
        total_pnl = sum((t.pnl or 0) for t in closed_trades)
        trading_fees = sum((t.trading_fees or 0) for t in closed_trades)
        funding_fees = sum((t.funding_fees or 0) for t in closed_trades)
        net_pnl = total_pnl - trading_fees - funding_fees
        
        # Advanced metrics
        winning_pnl = [float(t.pnl) for t in closed_trades if t.pnl and t.pnl > 0]
        losing_pnl = [float(t.pnl) for t in closed_trades if t.pnl and t.pnl <= 0]
        
        avg_win = sum(winning_pnl) / len(winning_pnl) if winning_pnl else 0
        avg_loss = sum(losing_pnl) / len(losing_pnl) if losing_pnl else 0
        profit_factor = (sum(winning_pnl) / abs(sum(losing_pnl))) if losing_pnl and sum(losing_pnl) != 0 else 0
        
        # Calculate average trade size and holding time
        total_order_size = sum(t.order_size for t in closed_trades)
        avg_trade_size = float(total_order_size / total_trades) if total_trades > 0 else 0
        
        # Calculate average holding time
        holding_times = [(t.closed_at - t.executed_at).total_seconds() / 3600 for t in closed_trades if t.closed_at]
        avg_holding_time = sum(holding_times) / len(holding_times) if holding_times else 0
        
        # Group by symbol
        symbol_data = {}
        for trade in closed_trades:
            symbol = trade.symbol
            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    "trades": 0,
                    "winning_trades": 0,
                    "pnl": 0,
                    "volume": 0
                }
            
            symbol_data[symbol]["trades"] += 1
            if trade.pnl and trade.pnl > 0:
                symbol_data[symbol]["winning_trades"] += 1
            symbol_data[symbol]["pnl"] += float(trade.pnl or 0)
            symbol_data[symbol]["volume"] += float(trade.order_size or 0)
        
        # Calculate win rate by symbol
        for symbol, data in symbol_data.items():
            if data["trades"] > 0:
                data["win_rate"] = (data["winning_trades"] / data["trades"]) * 100
            else:
                data["win_rate"] = 0
        
        logger.info(
            "Retrieved account performance",
            extra={
                "account_id": account_id,
                "total_trades": total_trades,
                "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
                "date_range": f"{start_date} to {end_date}"
            }
        )
        
        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": float(total_pnl),
            "trading_fees": float(trading_fees),
            "funding_fees": float(funding_fees),
            "net_pnl": float(net_pnl),
            "avg_trade_size": avg_trade_size,
            "avg_holding_time": avg_holding_time,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "by_symbol": symbol_data
        }
    
    @handle_db_error("Failed to get daily performance", lambda self, account_id, start_date, end_date: {"account_id": account_id, "date_range": f"{start_date} to {end_date}"})
    async def get_daily_performance(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get daily performance metrics for an account.
        """
        # Define pipeline for daily aggregation
        pipeline = [
            {
                "$match": {
                    "account_id": account_id,
                    "status": TradeStatus.CLOSED,
                    "closed_at": {"$gte": start_date, "$lte": end_date}
                }
            },
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$closed_at"}
                    },
                    "trades": {"$sum": 1},
                    "winning_trades": {"$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": {"$ifNull": ["$pnl", 0]}},
                    "trading_fees": {"$sum": {"$ifNull": ["$trading_fees", 0]}},
                    "funding_fees": {"$sum": {"$ifNull": ["$funding_fees", 0]}},
                    "order_size": {"$sum": {"$ifNull": ["$order_size", 0]}}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        
        # Execute aggregation
        results = await self.model.aggregate(pipeline).to_list()
        
        # Process results
        daily_stats = []
        for result in results:
            date = result.pop("_id")
            trades = result["trades"]
            winning_trades = result["winning_trades"]
            win_rate = (winning_trades / trades * 100) if trades > 0 else 0
            net_pnl = result["pnl"] - result["trading_fees"] - result["funding_fees"]
            
            daily_stats.append({
                "date": date,
                "trades": trades,
                "winning_trades": winning_trades,
                "win_rate": win_rate,
                "pnl": float(result["pnl"]),
                "trading_fees": float(result["trading_fees"]),
                "funding_fees": float(result["funding_fees"]),
                "net_pnl": float(net_pnl),
                "volume": float(result["order_size"])
            })
        
        logger.info(
            "Retrieved daily performance",
            extra={
                "account_id": account_id,
                "days": len(daily_stats),
                "date_range": f"{start_date} to {end_date}"
            }
        )
        
        return daily_stats

    # -------------------------------------------------------------
    # EXPORT FUNCTIONALITY 
    # -------------------------------------------------------------
    
    @handle_db_error("Failed to export trade history", lambda self, account_id, start_date, end_date: {"account_id": account_id, "date_range": f"{start_date} to {end_date}"})
    async def export_trade_history(
        self, 
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        format: str = "csv"
    ) -> Dict[str, Any]:
        """
        Export trade history for an account in the specified format.
        
        Args:
            account_id: Account ID
            start_date: Start date
            end_date: End date
            format: Export format ("csv" or "json")
            
        Returns:
            Dict with export data and metadata
        """
        # Get closed trades
        trades = await self.model.find({
            "account_id": account_id,
            "status": TradeStatus.CLOSED,
            "closed_at": {"$gte": start_date, "$lte": end_date}
        }).sort("closed_at", 1).to_list()
        
        # Format for export
        export_data = []
        for trade in trades:
            export_data.append({
                "trade_id": str(trade.id),
                "symbol": trade.symbol,
                "side": trade.side.value,
                "order_type": trade.order_type.value,
                "size": str(trade.size),
                "order_size": str(trade.order_size),
                "entry_price": str(trade.entry_price) if trade.entry_price else None,
                "exit_price": str(trade.exit_price) if trade.exit_price else None,
                "pnl": str(trade.pnl) if trade.pnl else None,
                "pnl_percentage": trade.pnl_percentage,
                "trading_fees": str(trade.trading_fees),
                "funding_fees": str(trade.funding_fees),
                "risk_percentage": str(trade.risk_percentage),
                "leverage": trade.leverage,
                "executed_at": trade.executed_at.isoformat(),
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
                "holding_time": str(trade.closed_at - trade.executed_at) if trade.closed_at else None,
                "source": trade.source.value
            })
        
        logger.info(
            "Exported trade history",
            extra={
                "account_id": account_id,
                "format": format,
                "trade_count": len(export_data),
                "date_range": f"{start_date} to {end_date}"
            }
        )
        
        # Return data with metadata
        return {
            "format": format,
            "account_id": account_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "trade_count": len(export_data),
            "data": export_data,
            "filename": f"trades_{account_id}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.{format}"
        }

    # -------------------------------------------------------------
    # HELPER METHODS
    # -------------------------------------------------------------
    
    @handle_db_error("Failed to retrieve trade by exchange ID", lambda self, exchange_id: {"exchange_id": exchange_id})
    async def get_by_exchange_id(self, exchange_id: str) -> Optional[Trade]:
        """Get a trade by its exchange order ID."""
        trade = await self.model.find_one({"exchange_order_id": exchange_id})
        if not trade:
            logger.debug(f"No trade found for exchange_id: {exchange_id}")
        return trade


# Create singleton instance for use throughout the application
trade = CRUDTrade(Trade)