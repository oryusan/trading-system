"""
Bot management endpoints focused solely on HTTP concerns.

This module delegates all business logic to the CRUD layer, which centralizes
service integration. The endpoints focus solely on:
- Request validation and parsing
- Response formatting
- Authentication and authorization
- Logging
"""

from fastapi import APIRouter, Depends, Query, Path, Request, status, HTTPException
from datetime import datetime
from typing import Dict, List, Optional, Any
from beanie import PydanticObjectId

# Import user dependencies
from app.api.v1.deps import get_current_active_user, get_admin_user, get_accessible_bots

# Import CRUD operations for centralized business logic
from app.crud.crud_bot import bot as bot_crud, BotCreate, BotUpdate, BotManualCreate

# Import core types and utilities
from app.core.errors.base import ValidationError, NotFoundError, AuthorizationError
from app.core.logging.logger import get_logger
from app.core.references import BotStatus, BotType, TimeFrame
from app.models.entities.user import User
from app.api.v1.references import ServiceResponse

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


@router.post("/create", response_model=ServiceResponse)
async def create_bot(
    request: Request,
    bot_data: BotCreate,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new bot (Admin only).
    """
    # Create bot via CRUD layer (which handles all service integration)
    new_bot = await bot_crud.create(bot_data)
    
    logger.info("Bot created", extra={
        "bot_id": str(new_bot.id),
        "name": new_bot.name,
        "user_id": str(current_user.id),
        "bot_type": new_bot.bot_type.value
    })
    
    return ServiceResponse(
        success=True,
        message="Bot created successfully",
        data={"bot": new_bot.to_dict()}
    )


@router.post("/manual", response_model=ServiceResponse)
async def create_manual_bot(
    request: Request,
    bot_data: BotManualCreate,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new manual trading bot.
    Admin only.
    """
    # Create manual bot via CRUD layer
    manual_bot = await bot_crud.create_manual_bot(bot_data)
    
    logger.info("Created manual bot", extra={
        "user_id": str(current_user.id),
        "bot_id": str(manual_bot.id),
        "name": manual_bot.name,
        "account_count": len(manual_bot.connected_accounts)
    })
    
    return ServiceResponse(
        success=True,
        message="Manual bot created successfully",
        data={"bot": manual_bot.to_dict()}
    )


@router.get("/list", response_model=ServiceResponse)
async def list_bots(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> ServiceResponse:
    """
    List bots accessible to the current user.
    """
    # Get bots via CRUD layer
    bots = []
    for bot_id in viewable_bots:
        try:
            bot = await bot_crud.get(PydanticObjectId(bot_id))
            bots.append(bot.to_dict())
        except Exception as e:
            logger.warning(
                f"Error retrieving bot {bot_id}",
                extra={"error": str(e), "bot_id": bot_id}
            )
    
    logger.info("Listed bots", extra={
        "user_id": str(current_user.id),
        "bot_count": len(bots)
    })
    
    return ServiceResponse(
        success=True,
        message="Bots retrieved successfully",
        data={"bots": bots}
    )


@router.get("/manual", response_model=ServiceResponse)
async def list_manual_bots(
    request: Request,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    List all manual trading bots.
    Admin only.
    """
    # Get manual bots via CRUD layer
    bots = await bot_crud.get_bots_by_type(BotType.MANUAL)
    
    logger.info("Listed manual bots", extra={
        "user_id": str(current_user.id),
        "bot_count": len(bots)
    })
    
    return ServiceResponse(
        success=True,
        message="Manual bots retrieved successfully",
        data={"bots": [bot.to_dict() for bot in bots]}
    )


@router.get("/automated", response_model=ServiceResponse)
async def list_automated_bots(
    request: Request,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    List all automated trading bots.
    Admin only.
    """
    # Get automated bots via CRUD layer
    bots = await bot_crud.get_bots_by_type(BotType.AUTOMATED)
    
    logger.info("Listed automated bots", extra={
        "user_id": str(current_user.id),
        "bot_count": len(bots)
    })
    
    return ServiceResponse(
        success=True,
        message="Automated bots retrieved successfully",
        data={"bots": [bot.to_dict() for bot in bots]}
    )


@router.get("/{bot_id}", response_model=ServiceResponse)
async def get_bot(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> ServiceResponse:
    """
    Get detailed bot information.
    """
    # Verify access to bot
    await verify_bot_access(bot_id, current_user, viewable_bots)
    
    # Get bot via CRUD layer
    bot = await bot_crud.get(PydanticObjectId(bot_id))
    
    # Get connected accounts with details
    accounts = await bot_crud.get_connected_accounts(PydanticObjectId(bot_id))
    
    # Get readiness status
    readiness = await bot_crud.verify_trading_ready(PydanticObjectId(bot_id))
    
    logger.info("Retrieved bot details", extra={
        "bot_id": bot_id,
        "user_id": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Bot retrieved successfully",
        data={
            "bot": bot.to_dict(),
            "accounts": accounts,
            "readiness": readiness
        }
    )


@router.patch("/{bot_id}", response_model=ServiceResponse)
async def update_bot(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    update_data: BotUpdate = ...,
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update bot settings.
    Admin only.
    """
    # Update bot via CRUD layer
    updated_bot = await bot_crud.update(
        PydanticObjectId(bot_id),
        update_data
    )
    
    logger.info("Updated bot", extra={
        "bot_id": bot_id,
        "fields": list(update_data.model_dump(exclude_unset=True).keys()),
        "modified_by": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Bot updated successfully",
        data={"bot": updated_bot.to_dict()}
    )


@router.post("/{bot_id}/status", response_model=ServiceResponse)
async def update_status(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    status: BotStatus = Query(..., description="New bot status"),
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update bot status (Admin only).
    
    - ACTIVE: Establish WebSocket connections.
    - PAUSED: Temporarily pause trading (retain connections for quick resume).
    - STOPPED: Fully shut down connections.
    """
    # Update status via CRUD layer (which handles WebSocket connections)
    updated_bot = await bot_crud.update_status(
        PydanticObjectId(bot_id),
        status
    )
    
    logger.info("Updated bot status", extra={
        "bot_id": bot_id,
        "status": status.value,
        "modified_by": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message=f"Bot status updated to {status.value}",
        data={"bot": updated_bot.to_dict()}
    )


@router.post("/{bot_id}/connect-account", response_model=ServiceResponse)
async def connect_account(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    account_id: str = Query(..., description="Account ID to connect"),
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Connect an account to a bot (Admin only).
    """
    # Connect account via CRUD layer
    updated_bot = await bot_crud.connect_accounts(
        PydanticObjectId(bot_id),
        [account_id]
    )
    
    logger.info("Connected account to bot", extra={
        "bot_id": bot_id,
        "account_id": account_id,
        "modified_by": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Account connected to bot successfully",
        data={"bot": updated_bot.to_dict()}
    )


@router.post("/{bot_id}/disconnect-account", response_model=ServiceResponse)
async def disconnect_account(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    account_id: str = Query(..., description="Account ID to disconnect"),
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Disconnect an account from a bot (Admin only).
    """
    # Disconnect account via CRUD layer
    updated_bot = await bot_crud.disconnect_accounts(
        PydanticObjectId(bot_id),
        [account_id]
    )
    
    logger.info("Disconnected account from bot", extra={
        "bot_id": bot_id,
        "account_id": account_id,
        "modified_by": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Account disconnected from bot successfully",
        data={"bot": updated_bot.to_dict()}
    )


@router.post("/{bot_id}/terminate", response_model=ServiceResponse)
async def terminate_bot(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    current_user: User = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Terminate all positions and orders for bot accounts (Admin only).
    """
    # Terminate positions via CRUD layer
    result = await bot_crud.terminate_positions(PydanticObjectId(bot_id))
    
    logger.info("Terminated bot positions", extra={
        "bot_id": bot_id,
        "success_count": result["success_count"],
        "error_count": result["error_count"],
        "modified_by": str(current_user.id)
    })
    
    return ServiceResponse(
        success=result["success"],
        message="Bot positions terminated successfully" if result["success"] else "Some positions could not be terminated",
        data=result
    )


@router.get("/{bot_id}/accounts", response_model=ServiceResponse)
async def get_connected_accounts(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> ServiceResponse:
    """
    Get accounts connected to a bot.
    """
    # Verify access to bot
    await verify_bot_access(bot_id, current_user, viewable_bots)
    
    # Get connected accounts via CRUD layer
    accounts = await bot_crud.get_connected_accounts(PydanticObjectId(bot_id))
    
    logger.info("Retrieved connected accounts", extra={
        "bot_id": bot_id,
        "account_count": len(accounts),
        "user_id": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Connected accounts retrieved successfully",
        data={"accounts": accounts}
    )


@router.get("/{bot_id}/performance", response_model=ServiceResponse)
async def get_bot_performance(
    request: Request,
    bot_id: str = Path(..., description="Bot ID"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_accessible_bots)
) -> ServiceResponse:
    """
    Get bot performance metrics.
    """
    # Verify access to bot
    await verify_bot_access(bot_id, current_user, viewable_bots)
    
    # Parse date parameters
    start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else datetime.utcnow().replace(day=1)
    end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
    
    # Get performance data via CRUD layer
    performance = await bot_crud.get_performance(
        PydanticObjectId(bot_id),
        start,
        end
    )
    
    logger.info("Retrieved bot performance", extra={
        "bot_id": bot_id,
        "date_range": f"{start_date or 'month-start'} to {end_date or 'now'}",
        "user_id": str(current_user.id)
    })
    
    return ServiceResponse(
        success=True,
        message="Bot performance retrieved successfully",
        data={
            "performance": performance,
            "date_range": {
                "start": start.isoformat(),
                "end": end.isoformat()
            }
        }
    )
