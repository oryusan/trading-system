"""
Bot management endpoints with comprehensive error handling.

Features:
- Bot creation and validation
- Account connection management
- Status monitoring and updates
- Performance tracking
"""

from fastapi import APIRouter, Depends, status
from datetime import datetime
from typing import Dict, List, Optional

# Basic dependency imports
from app.api.v1.deps import get_current_active_user, get_admin_user, get_accessible_bots

from app.core.errors.base import AuthorizationError, NotFoundError
from app.core.logging.logger import get_logger
from app.core.references import TimeFrame, BotStatus
from app.models.entities.user import User
from app.services.exchange.operations import ExchangeOperations
from app.services.reference.manager import reference_manager
from app.services.trading.service import trading_service
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager

router = APIRouter()
logger = get_logger(__name__)


async def verify_bot_access(bot_id: str, current_user: User, viewable_bots: List[str]) -> None:
    """
    Verify that the given bot_id is within the list of viewable bots.
    Raises an AuthorizationError if access is not allowed.
    """
    if bot_id not in viewable_bots:
        context = {"bot_id": bot_id, "user_id": str(current_user.id)}
        raise AuthorizationError("Not authorized to view this bot", context=context)


@router.post("/create", response_model=Dict)
async def create_bot(
    name: str,
    base_name: str,
    timeframe: TimeFrame,
    max_drawdown: float = 60.0,
    risk_limit: float = 6.0,
    max_allocation: float = 369000.0,
    min_account_balance: float = 100.0,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Create a new bot (Admin only)."""
    context = {
        "name": name,
        "base_name": base_name,
        "timeframe": timeframe,
        "max_drawdown": max_drawdown,
        "risk_limit": risk_limit,
        "max_allocation": max_allocation,
        "min_account_balance": min_account_balance,
        "user_id": str(current_user.id)
    }
    bot_data = {
        "name": name,
        "base_name": base_name,
        "timeframe": timeframe,
        "status": BotStatus.STOPPED,
        "max_drawdown": max_drawdown,
        "risk_limit": risk_limit,
        "max_allocation": max_allocation,
        "min_account_balance": min_account_balance
    }
    bot_id = await reference_manager.create_reference(
        source_type="Bot",
        data=bot_data,
        validate=True
    )
    logger.info("Bot created", extra={**context, "bot_id": str(bot_id)})
    return {"success": True, "bot_id": str(bot_id), **bot_data}


@router.get("/list", response_model=List[Dict])
async def list_bots(
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> List[Dict]:
    """List bots accessible to the current user."""
    context = {"user_id": str(current_user.id)}
    bots = await reference_manager.get_references(
        source_type="User",
        reference_id=str(current_user.id),
        target_type="Bot",
        filter_ids=viewable_bots
    )
    logger.info("Listed bots", extra={**context, "bot_count": len(bots)})
    return bots


@router.get("/{bot_id}", response_model=Dict)
async def get_bot(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> Dict:
    """Get bot details if accessible."""
    context = {"bot_id": bot_id, "user_id": str(current_user.id)}
    await verify_bot_access(bot_id, current_user, viewable_bots)
    bot = await reference_manager.get_reference(
        source_type="Bot",
        reference_id=bot_id
    )
    if not bot:
        raise NotFoundError("Bot not found", context={"bot_id": bot_id})
    # Add performance metrics
    metrics = await performance_service.get_bot_metrics(bot_id)
    bot["performance"] = metrics
    logger.info("Retrieved bot details", extra=context)
    return bot

@router.patch("/{bot_id}", response_model=ServiceResponse)
async def update_bot(
    bot_id: str,
    update_data: BotUpdate,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update bot settings.

    This endpoint allows an admin to update bot properties, including operational status and 
    configuration parameters (e.g., max_drawdown, risk_limit, max_allocation, min_account_balance).
    """
    updated_bot = await bot_crud.update(bot_id, update_data)
    return ServiceResponse(
        success=True,
        message="Bot updated successfully",
        data={"bot": updated_bot.to_dict()}
    )

@router.post("/{bot_id}/status", response_model=Dict)
async def update_status(
    bot_id: str,
    status: BotStatus,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """
    Update bot status (Admin only).
    
    - ACTIVE: Establish WebSocket connections.
    - PAUSED: Temporarily pause trading (retain connections for quick resume).
    - STOPPED: Fully shut down connections.
    """
    context = {"bot_id": bot_id, "status": status, "user_id": str(current_user.id)}
    await reference_manager.update_reference(
        source_type="Bot",
        reference_id=bot_id,
        updates={
            "status": status,
            "modified_at": datetime.utcnow()
        },
        validate=True
    )
    # Update WebSocket connections based on the new status.
    if status == BotStatus.ACTIVE:
        await ws_manager.setup_bot_connections(bot_id)
    elif status == BotStatus.PAUSED:
        logger.info("Bot paused; retaining active connections for quick resume", extra=context)
    elif status == BotStatus.STOPPED:
        await ws_manager.cleanup_bot_connections(bot_id)
    
    logger.info("Updated bot status", extra=context)
    return {"success": True, "status": status}


@router.post("/{bot_id}/connect-account", response_model=Dict)
async def connect_account(
    bot_id: str,
    account_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Connect an account to a bot (Admin only)."""
    context = {"bot_id": bot_id, "account_id": account_id, "user_id": str(current_user.id)}
    await reference_manager.validate_references(
        source_type="Bot",
        references={"bot": bot_id, "account": account_id}
    )
    await trading_service.connect_bot_account(bot_id=bot_id, account_id=account_id)
    logger.info("Connected account to bot", extra=context)
    return {"success": True, "message": "Account connected"}


@router.post("/{bot_id}/disconnect-account", response_model=Dict)
async def disconnect_account(
    bot_id: str,
    account_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Disconnect an account from a bot (Admin only)."""
    context = {"bot_id": bot_id, "account_id": account_id, "user_id": str(current_user.id)}
    await trading_service.disconnect_bot_account(bot_id=bot_id, account_id=account_id)
    logger.info("Disconnected account from bot", extra=context)
    return {"success": True, "message": "Account disconnected"}


@router.post("/{bot_id}/terminate", response_model=Dict)
async def terminate_bot(
    bot_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Terminate all positions and orders for bot accounts (Admin only)."""
    context = {"bot_id": bot_id, "user_id": str(current_user.id)}
    result = await ExchangeOperations.terminate_bot_accounts(
        bot_id=bot_id,
        reference_manager=reference_manager
    )
    if result.get("success"):
        await reference_manager.update_reference(
            source_type="Bot",
            reference_id=bot_id,
            updates={
                "status": BotStatus.PAUSED,
                "modified_at": datetime.utcnow()
            }
        )
    logger.info("Bot terminated for now", extra={**context, "terminated_accounts": result.get("terminated_accounts", 0)})
    return result


@router.get("/{bot_id}/accounts", response_model=List[Dict])
async def get_connected_accounts(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> List[Dict]:
    """Get accounts connected to a bot."""
    context = {"bot_id": bot_id, "user_id": str(current_user.id)}
    await verify_bot_access(bot_id, current_user, viewable_bots)
    accounts = await reference_manager.get_references(
        source_type="Bot",
        reference_id=bot_id,
        target_type="Account"
    )
    logger.info("Retrieved connected accounts", extra={**context, "account_count": len(accounts)})
    return accounts


@router.get("/{bot_id}/performance", response_model=Dict)
async def get_bot_performance(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> Dict:
    """Get bot performance metrics."""
    context = {"bot_id": bot_id, "user_id": str(current_user.id)}
    await verify_bot_access(bot_id, current_user, viewable_bots)
    metrics = await performance_service.get_bot_metrics(
        bot_id=bot_id,
        include_accounts=True
    )
    logger.info("Retrieved bot performance", extra={**context, "metrics": list(metrics.keys())})
    return metrics
