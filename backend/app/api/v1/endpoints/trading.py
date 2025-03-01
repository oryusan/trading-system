"""
Trading endpoints with standardized error handling and service delegation.

Features:
- Clean API endpoint handlers focused on HTTP concerns
- Proper input validation and authorization
- Consistent error responses
- Delegated business logic to trading service
- Enhanced logging and context tracking
"""

from fastapi import APIRouter, Depends, Request, Path, Query, HTTPException, status
from datetime import datetime
from typing import Dict, List, Optional, Any
from beanie import PydanticObjectId
from pydantic import BaseModel, Field, validator

from app.api.v1.deps import get_current_active_user, get_admin_user, get_accessible_accounts
from app.api.v1.references import ServiceResponse
from app.core.errors.base import AuthorizationError, NotFoundError, ValidationError, ExchangeError
from app.core.logging.logger import get_logger
from app.core.references import OrderType, TimeFrame, TradeStatus, PositionSide, TradeSource, SignalOrderType, BotType
from app.crud.crud_trade import trade as trade_crud, TradeCreate
from app.crud.crud_bot import bot as bot_crud
from app.models.entities.trade import Trade
from app.models.entities.bot import Bot
from app.services.trading.service import trading_service

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------
# Request Models
# ---------------------------
class BotTradeRequest(BaseModel):
    """Bot trade request validation."""
    symbol: str = Field(..., description="Trading symbol")
    side: PositionSide = Field(..., description="Trade side (buy/sell)")
    signal_type: SignalOrderType = Field(..., description="Signal type (LONG_SIGNAL, SHORT_SIGNAL, etc.)")
    risk_percentage: float = Field(..., gt=0, le=5, description="Risk percentage")
    leverage: int = Field(..., gt=0, le=100, description="Position leverage")
    take_profit: Optional[float] = Field(None, description="Take profit price")
    stop_loss: Optional[float] = Field(None, description="Stop loss price")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        return v.upper()


class GroupTradeRequest(BotTradeRequest):
    """Legacy group trade request validation."""
    pass


class PositionCloseRequest(BaseModel):
    """Position close request validation."""
    symbol: str = Field(..., description="Symbol to close")
    order_type: OrderType = Field(..., description="Close order type")
    exit_price: Optional[float] = Field(None, description="Manual exit price (if not market)")


# ---------------------------
# Helper Functions
# ---------------------------
def get_request_context(request: Request, **extras) -> Dict[str, Any]:
    """Create standardized request context for logging and error tracking."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "path": request.url.path,
        "method": request.method,
        "client_ip": request.client.host,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if extras:
        context.update(extras)
    return context


async def verify_account_access(account_id: str, current_user: Any, allowed_accounts: List[str]) -> None:
    """Verify the user has access to the specified account."""
    if account_id not in allowed_accounts:
        raise AuthorizationError(
            "Not authorized to access this account", 
            context={"account_id": account_id, "user_id": str(current_user.user_id)}
        )


# ---------------------------
# Bot-Centric Trading Endpoints
# ---------------------------
@router.post("/bot/{bot_id}/execute")
async def execute_bot_trade(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    trade_params: BotTradeRequest = ...,
    current_user: Any = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Execute a trade for all accounts connected to a bot.
    Admin only.
    """
    context = get_request_context(
        request, 
        bot_id=bot_id, 
        params=trade_params.dict(), 
        user_id=str(current_user.user_id)
    )
    
    logger.info("Processing bot trade execution", extra=context)
    
    # Get bot and verify it exists
    bot = await bot_crud.get(PydanticObjectId(bot_id))
    
    if not bot.connected_accounts:
        return ServiceResponse(
            success=True,
            message="No accounts connected to bot",
            data={"bot_id": bot_id, "accounts_processed": 0}
        )
    
    # Process trades using the trading service's process_signal method
    results = await trading_service.process_signal(
        bot_id=bot_id,
        signal_data={
            "symbol": trade_params.symbol,
            "side": trade_params.side,
            "signal_type": trade_params.signal_type,
            "risk_percentage": trade_params.risk_percentage,
            "leverage": trade_params.leverage,
            "take_profit": trade_params.take_profit,
            "stop_loss": trade_params.stop_loss,
            "source": TradeSource.TRADING_PANEL
        },
        context=context
    )
    
    logger.info(
        "Bot trade execution completed",
        extra={
            **context,
            "success_count": results["success_count"],
            "total_accounts": len(bot.connected_accounts),
            "error_count": results["error_count"]
        }
    )
    
    return ServiceResponse(
        success=results["success"],
        message=f"Executed trades for {results['success_count']} of {results['accounts_processed']} accounts",
        data=results
    )


# ---------------------------
# Legacy Group-based Endpoints (for Backward Compatibility)
# ---------------------------
@router.post("/group/{group_id}/execute")
async def execute_group_trade(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    trade_params: GroupTradeRequest = ...,
    current_user: Any = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Legacy endpoint to execute a trade for all accounts in a group.
    This is maintained for backward compatibility but internally uses the bot-centric approach.
    Admin only.
    """
    context = get_request_context(
        request, 
        group_id=group_id, 
        params=trade_params.dict(), 
        user_id=str(current_user.user_id)
    )
    
    logger.info("Processing group trade execution (legacy endpoint)", extra=context)
    
    # Get accounts from the group
    from app.crud.crud_group import group as group_crud
    group = await group_crud.get(PydanticObjectId(group_id))
    
    if not group.accounts:
        return ServiceResponse(
            success=True,
            message="No accounts in group to execute trade",
            data={"group_id": group_id, "accounts_processed": 0}
        )
    
    # Create a temporary bot object for execution (not saved to database)
    # This is a clean approach for the migration path
    bot_name = f"LegacyGroupTrade-{group_id[:8]}"
    temp_bot = Bot(
        id=PydanticObjectId(),  # Generate a temporary ID
        name=bot_name,
        base_name=bot_name,
        timeframe=TimeFrame.D1,
        status=BotStatus.ACTIVE,
        bot_type=BotType.MANUAL,
        connected_accounts=group.accounts.copy(),
        created_at=datetime.utcnow()
    )
    
    # Process trades using the trading service's process_signal method
    results = await trading_service.process_signal(
        bot_id=str(temp_bot.id),
        signal_data={
            "symbol": trade_params.symbol,
            "side": trade_params.side,
            "signal_type": trade_params.signal_type,
            "risk_percentage": trade_params.risk_percentage,
            "leverage": trade_params.leverage,
            "take_profit": trade_params.take_profit,
            "stop_loss": trade_params.stop_loss,
            "source": TradeSource.TRADING_PANEL,
            "accounts": group.accounts  # Direct mapping of accounts
        },
        context=context
    )
    
    logger.info(
        "Legacy group trade execution completed",
        extra={
            **context,
            "success_count": results["success_count"],
            "total_accounts": len(group.accounts),
            "error_count": results["error_count"]
        }
    )
    
    return ServiceResponse(
        success=results["success"],
        message=f"Executed trades for {results['success_count']} of {results['accounts_processed']} accounts",
        data=results
    )


# ---------------------------
# Account-based Operations
# ---------------------------
@router.get("/positions/{account_id}")
async def get_positions(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    status: Optional[TradeStatus] = Query(None, description="Filter by trade status"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of trades to return"),
    current_user: Any = Depends(get_current_active_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Retrieve recent trades/positions for the given account.
    User must have access to the account.
    """
    context = get_request_context(
        request, 
        account_id=account_id, 
        filters={"status": status.value if status else None, "symbol": symbol},
        user_id=str(current_user.user_id)
    )
    
    # Verify access to account
    await verify_account_access(account_id, current_user, allowed_accounts)
    
    # Get trades/positions from CRUD layer
    trades = await trade_crud.get_account_trades(
        account_id=account_id,
        status=status,
        symbol=symbol,
        limit=limit
    )
    
    # Format response
    formatted_trades = [trade.get_trade_info() for trade in trades]
    
    logger.info(
        "Retrieved account positions",
        extra={**context, "count": len(trades)}
    )
    
    return ServiceResponse(
        success=True,
        message=f"Retrieved {len(trades)} positions",
        data={
            "account_id": account_id,
            "count": len(trades),
            "positions": formatted_trades
        }
    )


@router.post("/close-position/{account_id}")
async def close_position(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    close_params: PositionCloseRequest = ...,
    current_user: Any = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Close a position for the given account.
    Admin only.
    """
    context = get_request_context(
        request, 
        account_id=account_id, 
        symbol=close_params.symbol,
        user_id=str(current_user.user_id)
    )
    
    logger.info("Processing position close request", extra=context)
    
    # Get open position by symbol
    positions = await trade_crud.get_open_positions(account_id, close_params.symbol)
    
    if not positions:
        return ServiceResponse(
            success=False,
            message=f"No open position found for {close_params.symbol}",
            data={"account_id": account_id, "symbol": close_params.symbol}
        )
    
    if len(positions) > 1:
        logger.warning(
            f"Multiple open positions found for {close_params.symbol}",
            extra={**context, "count": len(positions)}
        )
    
    # Close the position using trading service
    try:
        close_result = await trading_service.close_position(
            account_id=account_id,
            symbol=close_params.symbol,
            order_type=close_params.order_type,
            manual_price=close_params.exit_price
        )
        
        # Update trade in database using CRUD layer
        position = positions[0]  # Close the first position if multiple exist
        closed_trade = await trade_crud.close_trade(
            trade_id=position.id,
            exit_data={
                "exit_price": close_result["exit_price"],
                "trading_fees": close_result.get("trading_fees", 0),
                "funding_fees": close_result.get("funding_fees", 0)
            }
        )
        
        logger.info(
            "Position closed successfully",
            extra={
                **context,
                "trade_id": str(closed_trade.id),
                "exit_price": str(closed_trade.exit_price),
                "pnl": str(closed_trade.pnl)
            }
        )
        
        return ServiceResponse(
            success=True,
            message=f"Position closed successfully",
            data={
                "account_id": account_id,
                "symbol": close_params.symbol,
                "trade_id": str(closed_trade.id),
                "pnl": str(closed_trade.pnl),
                "pnl_percentage": closed_trade.pnl_percentage,
                "exit_price": str(closed_trade.exit_price)
            }
        )
    
    except Exception as e:
        logger.error(
            "Failed to close position",
            extra={**context, "error": str(e)}
        )
        
        return ServiceResponse(
            success=False,
            message=f"Failed to close position: {str(e)}",
            data={"account_id": account_id, "symbol": close_params.symbol}
        )


@router.post("/terminate/{account_id}")
async def terminate_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Any = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Terminate all positions for the given account.
    Admin only.
    """
    context = get_request_context(
        request, 
        account_id=account_id, 
        user_id=str(current_user.user_id)
    )
    
    logger.info("Processing account termination request", extra=context)
    
    # Get all open positions
    open_positions = await trade_crud.get_open_positions(account_id)
    
    if not open_positions:
        return ServiceResponse(
            success=True,
            message="No open positions to terminate",
            data={"account_id": account_id, "positions_count": 0}
        )
    
    # Terminate all positions via trading service
    try:
        termination_result = await trading_service.terminate_account(account_id)
        
        # Prepare close data for updating the database
        close_data = {}
        for position_result in termination_result.get("positions", []):
            if position_result.get("success", False):
                symbol = position_result.get("symbol")
                close_data[symbol] = {
                    "exit_price": position_result.get("exit_price"),
                    "trading_fees": position_result.get("trading_fees", 0),
                    "funding_fees": position_result.get("funding_fees", 0)
                }
        
        # Close all positions in the database
        close_results = await trade_crud.close_all_positions(account_id, close_data)
        
        logger.info(
            "Account positions terminated",
            extra={
                **context,
                "positions_count": len(open_positions),
                "closed_count": close_results.get("closed_count", 0)
            }
        )
        
        return ServiceResponse(
            success=True,
            message=f"Terminated {close_results.get('closed_count', 0)} of {len(open_positions)} positions",
            data={
                "account_id": account_id,
                "total_positions": len(open_positions),
                "terminated": close_results.get("closed_count", 0),
                "positions": close_results.get("positions", [])
            }
        )
    
    except Exception as e:
        logger.error(
            "Failed to terminate account positions",
            extra={**context, "error": str(e)}
        )
        
        return ServiceResponse(
            success=False,
            message=f"Failed to terminate positions: {str(e)}",
            data={"account_id": account_id, "positions_count": len(open_positions)}
        )


@router.get("/account/{account_id}/performance")
async def get_account_performance(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    current_user: Any = Depends(get_current_active_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Get performance metrics for an account.
    User must have access to the account.
    """
    context = get_request_context(
        request, 
        account_id=account_id, 
        date_range={"start": start_date, "end": end_date},
        user_id=str(current_user.user_id)
    )
    
    # Verify access to account
    await verify_account_access(account_id, current_user, allowed_accounts)
    
    # Parse dates
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        
        if start > end:
            raise ValidationError(
                "Start date must be before end date", 
                context={"start_date": start_date, "end_date": end_date}
            )
    except ValueError:
        raise ValidationError(
            "Invalid date format", 
            context={"start_date": start_date, "end_date": end_date, "expected_format": "YYYY-MM-DD"}
        )
    
    # Get performance data
    performance = await trade_crud.get_account_performance(
        account_id=account_id,
        start_date=start,
        end_date=end
    )
    
    # Get daily performance
    daily_data = await trade_crud.get_daily_performance(
        account_id=account_id,
        start_date=start,
        end_date=end
    )
    
    logger.info(
        "Retrieved account performance",
        extra={
            **context,
            "total_trades": performance.get("total_trades", 0),
            "win_rate": performance.get("win_rate", 0)
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Performance data retrieved successfully",
        data={
            "account_id": account_id,
            "date_range": {
                "start": start_date,
                "end": end_date
            },
            "summary": performance,
            "daily": daily_data
        }
    )


@router.get("/account/{account_id}/export")
async def export_trade_history(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    format: str = Query("csv", description="Export format (csv or json)"),
    current_user: Any = Depends(get_current_active_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Export trade history for an account.
    User must have access to the account.
    """
    context = get_request_context(
        request, 
        account_id=account_id, 
        date_range={"start": start_date, "end": end_date},
        format=format,
        user_id=str(current_user.user_id)
    )
    
    # Verify access to account
    await verify_account_access(account_id, current_user, allowed_accounts)
    
    # Parse dates
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        
        if start > end:
            raise ValidationError(
                "Start date must be before end date", 
                context={"start_date": start_date, "end_date": end_date}
            )
    except ValueError:
        raise ValidationError(
            "Invalid date format", 
            context={"start_date": start_date, "end_date": end_date, "expected_format": "YYYY-MM-DD"}
        )
    
    # Generate export
    export_data = await trade_crud.export_trade_history(
        account_id=account_id,
        start_date=start,
        end_date=end,
        format=format
    )
    
    logger.info(
        "Exported trade history",
        extra={
            **context,
            "trade_count": export_data.get("trade_count", 0)
        }
    )
    
    return ServiceResponse(
        success=True,
        message=f"Generated export with {export_data.get('trade_count', 0)} trades",
        data=export_data
    )
