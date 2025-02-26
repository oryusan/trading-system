"""
Trading endpoints with comprehensive error handling and safety measures.

Features:
- Enhanced error handling via global exception handlers
- Service integration
- Reference validation
- Performance tracking
"""

from fastapi import APIRouter, Depends, Request
from datetime import datetime
from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field

router = APIRouter()

# Import necessary dependencies and services.
from app.api.v1.deps import get_current_active_user
from app.core.errors.base import AuthorizationError, NotFoundError
from app.core.logging.logger import get_logger
from app.core.references import OrderType, UserRole
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.trading.service import trading_service

logger = get_logger(__name__)


def build_context(request: Request, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build a standardized context dictionary for logging and error handling.
    """
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "timestamp": datetime.utcnow().isoformat()
    }
    if extra:
        context.update(extra)
    return context


def check_admin(current_user: Any, action: str) -> None:
    """
    Ensure that the current user has admin privileges.
    Raises an AuthorizationError if not.
    """
    if current_user.role != UserRole.ADMIN:
        raise AuthorizationError(
            f"Admin privileges required to {action}",
            context={"user_id": str(current_user.id), "role": current_user.role}
        )


# ---------------------------
# Request Models
# ---------------------------
class GroupTradeRequest(BaseModel):
    """Group trade request validation."""
    symbol: str = Field(..., description="Trading symbol")
    side: str = Field(..., description="Trade side (buy/sell)")
    order_type: OrderType = Field(..., description="Order type")
    risk_percentage: float = Field(..., gt=0, le=5, description="Risk percentage")
    leverage: int = Field(..., gt=0, le=100, description="Position leverage")
    take_profit: Optional[float] = Field(None, description="Take profit price")


class PositionCloseRequest(BaseModel):
    """Position close request validation."""
    symbol: str = Field(..., description="Symbol to close")
    order_type: OrderType = Field(..., description="Close order type")


# ---------------------------
# Endpoints
# ---------------------------
@router.post("/group/{group_id}/execute")
async def execute_group_trade(
    request: Request,
    group_id: str,
    trade_params: GroupTradeRequest,
    current_user: Any = Depends(get_current_active_user)
) -> Dict:
    """
    Execute a trade for all accounts in a group.
    """
    context = build_context(request, {
        "group_id": group_id,
        "trade_params": trade_params.dict(),
        "user_id": str(current_user.id)
    })
    # Validate admin privileges.
    check_admin(current_user, "execute group trades")
    # Validate that the group exists.
    group = await reference_manager.get_reference(
        reference_id=group_id,
        source_type="Group"
    )
    if not group:
        raise NotFoundError("Group not found", context={"group_id": group_id})
    # Execute trades.
    trade_service_instance = await reference_manager.get_service("TradeService")
    results = await trade_service_instance.execute_group_trade(
        group_id=group_id,
        trade_params=trade_params.dict(),
        context=context
    )
    # Calculate performance metrics.
    results_list = results.get("results", [])
    trades_executed = len(results_list)
    success_count = results.get("success_count", 0)
    success_rate = (success_count / trades_executed * 100) if trades_executed > 0 else 0
    await performance_service.update_group_metrics(
        group_id=group_id,
        metrics={
            "trades_executed": trades_executed,
            "success_rate": success_rate
        }
    )
    logger.info(
        "Group trade execution completed",
        extra={**context, "success_count": success_count, "error_count": results.get("error_count", 0)}
    )
    return results


@router.get("/positions/{account_id}")
async def get_positions(
    request: Request,
    account_id: str,
    limit: int = 50,
    current_user: Any = Depends(get_current_active_user)
) -> List[Dict]:
    """
    Retrieve recent closed positions for the given account.
    """
    context = build_context(request, {
        "account_id": account_id,
        "limit": limit,
        "user_id": str(current_user.id)
    })
    # Validate access to the account.
    has_access = await reference_manager.validate_access(
        user_id=str(current_user.id),
        resource_type="Account",
        resource_id=account_id
    )
    if not has_access:
        raise AuthorizationError(
            "Not authorized to view account positions",
            context={"account_id": account_id, "user_id": str(current_user.id)}
        )
    # Retrieve positions via the trade service.
    trade_service_instance = await reference_manager.get_service("TradeService")
    positions = await trade_service_instance.get_account_positions(
        account_id=account_id,
        limit=limit
    )
    logger.info(
        "Retrieved account positions",
        extra={**context, "position_count": len(positions)}
    )
    return positions


@router.post("/close-position/{account_id}")
async def close_position(
    request: Request,
    account_id: str,
    close_params: PositionCloseRequest,
    current_user: Any = Depends(get_current_active_user)
) -> Dict:
    """
    Close a position for the given account.
    """
    context = build_context(request, {
        "account_id": account_id,
        "close_params": close_params.dict(),
        "user_id": str(current_user.id)
    })
    # Validate admin privileges.
    check_admin(current_user, "close positions")
    # Close the position via the trade service.
    trade_service_instance = await reference_manager.get_service("TradeService")
    result = await trade_service_instance.close_position(
        account_id=account_id,
        symbol=close_params.symbol,
        order_type=close_params.order_type,
        context=context
    )
    logger.info(
        "Position closed successfully",
        extra={**context, "success": result.get("success", False)}
    )
    return result


@router.post("/terminate/{account_id}")
async def terminate_account(
    request: Request,
    account_id: str,
    current_user: Any = Depends(get_current_active_user)
) -> Dict:
    """
    Terminate all positions for the given account.
    """
    context = build_context(request, {
        "account_id": account_id,
        "user_id": str(current_user.id)
    })
    # Validate admin privileges.
    check_admin(current_user, "terminate accounts")
    # Terminate the account via the trade service.
    trade_service_instance = await reference_manager.get_service("TradeService")
    result = await trade_service_instance.terminate_account(
        account_id=account_id,
        context=context
    )
    logger.info(
        "Account terminated successfully",
        extra={**context, "success": result.get("success", False)}
    )
    return result
