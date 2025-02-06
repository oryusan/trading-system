"""
Trading endpoints with comprehensive error handling and safety measures.

Features:
- Enhanced error handling
- Service integration
- Reference validation
- Performance tracking
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

# FastAPI validation schemas
from pydantic import BaseModel, Field

router = APIRouter()
logger = get_logger(__name__)

# Request Models
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

@router.post("/group/{group_id}/execute")
async def execute_group_trade(
    request: Request,
    group_id: str,
    trade_params: GroupTradeRequest,
    current_user: User = Depends(get_current_active_user)
) -> Dict:
    """Execute trade for all accounts in group with enhanced validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "group_id": group_id,
        "trade_params": trade_params.dict(),
        "user_id": str(current_user.id),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        # Validate permissions
        if current_user.role != UserRole.ADMIN:
            raise AuthorizationError(
                "Admin privileges required for group trades",
                context={
                    "user_id": str(current_user.id),
                    "role": current_user.role
                }
            )

        # Get group with validation
        group = await reference_manager.get_reference(
            reference_id=group_id,
            source_type="Group"
        )
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        # Execute trades with tracking
        trade_service = await reference_manager.get_service("TradeService") 
        results = await trade_service.execute_group_trade(
            group_id=group_id,
            trade_params=trade_params.dict(),
            context=context
        )

        # Update performance metrics
        await performance_service.update_group_metrics(
            group_id=group_id,
            metrics={
                "trades_executed": len(results.get("results", [])),
                "success_rate": results.get("success_count", 0) / len(results.get("results", [])) * 100
            }
        )

        logger.info(
            "Group trade execution completed",
            extra={
                **context,
                "success_count": results.get("success_count", 0),
                "error_count": results.get("error_count", 0)
            }
        )

        return results

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Group trade execution failed"
        )

@router.get("/positions/{account_id}")
async def get_positions(
    request: Request,
    account_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_active_user)
) -> List[Dict]:
    """Get recent closed positions with enhanced validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "account_id": account_id,
        "limit": limit,
        "user_id": str(current_user.id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Validate account access
        if not await reference_manager.validate_access(
            user_id=str(current_user.id),
            resource_type="Account",
            resource_id=account_id
        ):
            raise AuthorizationError(
                "Not authorized to view account positions",
                context={
                    "account_id": account_id,
                    "user_id": str(current_user.id)
                }
            )

        # Get positions from service
        trade_service = await reference_manager.get_service("TradeService")
        positions = await trade_service.get_account_positions(
            account_id=account_id,
            limit=limit
        )

        logger.info(
            "Retrieved account positions",
            extra={
                **context,
                "position_count": len(positions)
            }
        )

        return positions

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get account positions"
        )

@router.post("/close-position/{account_id}")
async def close_position(
    request: Request,
    account_id: str,
    close_params: PositionCloseRequest,
    current_user: User = Depends(get_current_active_user)
) -> Dict:
    """Close position with enhanced safety checks."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "account_id": account_id,
        "close_params": close_params.dict(),
        "user_id": str(current_user.id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Validate admin permissions
        if current_user.role != UserRole.ADMIN:
            raise AuthorizationError(
                "Admin privileges required to close positions",
                context={
                    "user_id": str(current_user.id),
                    "role": current_user.role
                }
            )

        # Close position via service
        trade_service = await reference_manager.get_service("TradeService")
        result = await trade_service.close_position(
            account_id=account_id,
            symbol=close_params.symbol,
            order_type=close_params.order_type,
            context=context
        )

        logger.info(
            "Position closed successfully",
            extra={
                **context,
                "success": result.get("success", False)
            }
        )

        return result

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to close position"
        )

@router.post("/terminate/{account_id}")
async def terminate_account(
    request: Request,
    account_id: str,
    current_user: User = Depends(get_current_active_user)
) -> Dict:
    """Terminate all positions with safety validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "account_id": account_id,
        "user_id": str(current_user.id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Validate admin permissions
        if current_user.role != UserRole.ADMIN:
            raise AuthorizationError(
                "Admin privileges required to terminate accounts",
                context={
                    "user_id": str(current_user.id),
                    "role": current_user.role
                }
            )

        # Terminate via service
        trade_service = await reference_manager.get_service("TradeService")
        result = await trade_service.terminate_account(
            account_id=account_id,
            context=context
        )

        logger.info(
            "Account terminated successfully",
            extra={
                **context,
                "success": result.get("success", False)
            }
        )

        return result

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to terminate account"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    AuthorizationError,
    ValidationError,
    ExchangeError,
    NotFoundError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    OrderType,
    TradeSource,
    UserRole
)
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.api.v1.deps import get_current_active_user