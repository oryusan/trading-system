"""
Bot management endpoints with comprehensive error handling.

Features:
- Bot creation and validation
- Account connection management
- Status monitoring and updates
- Position management
- Performance tracking
"""

from fastapi import APIRouter, Depends, status
from datetime import datetime
from typing import Dict, List, Optional

# Import dependencies
from app.api.v1.deps import (
    get_current_active_user, 
    get_admin_user,
    get_viewable_bots
)

router = APIRouter()
logger = get_logger(__name__)

@router.post("/create", response_model=Dict)
async def create_bot(
    name: str,
    base_name: str,
    timeframe: TimeFrame,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Create a new bot (Admin only)."""
    context = {
        "name": name,
        "base_name": base_name,
        "timeframe": timeframe,
        "user_id": str(current_user.id)
    }
    
    try:
        # Validate bot via reference manager
        bot_data = {
            "name": name,
            "base_name": base_name,
            "timeframe": timeframe,
            "status": BotStatus.STOPPED
        }
        
        bot_id = await reference_manager.create_reference(
            source_type="Bot",
            data=bot_data,
            validate=True
        )

        logger.info(
            "Bot created",
            extra={
                "bot_id": str(bot_id),
                **context
            }
        )

        return {
            "success": True,
            "bot_id": str(bot_id)
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Bot creation failed"
        )

@router.get("/list", response_model=List[Dict])
async def list_bots(
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_viewable_bots)
) -> List[Dict]:
    """List bots accessible to current user."""
    context = {
        "user_id": str(current_user.id)
    }
    
    try:
        bots = await reference_manager.get_references(
            source_type="User",
            reference_id=str(current_user.id),
            target_type="Bot",
            filter_ids=viewable_bots
        )

        logger.info(
            "Listed bots",
            extra={
                **context,
                "bot_count": len(bots)
            }
        )

        return bots

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to list bots"
        )

@router.get("/{bot_id}", response_model=Dict)
async def get_bot(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_viewable_bots)
) -> Dict:
    """Get bot details if accessible."""
    context = {
        "bot_id": bot_id,
        "user_id": str(current_user.id)
    }
    
    try:
        if bot_id not in viewable_bots:
            raise AuthorizationError(
                "Not authorized to view this bot",
                context=context
            )

        bot = await reference_manager.get_reference(
            source_type="Bot",
            reference_id=bot_id
        )

        if not bot:
            raise NotFoundError(
                "Bot not found",
                context={"bot_id": bot_id}
            )

        # Add performance metrics
        metrics = await performance_service.get_bot_metrics(bot_id)
        bot["performance"] = metrics

        logger.info(
            "Retrieved bot details",
            extra=context
        )

        return bot

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get bot details"
        )

@router.post("/{bot_id}/status", response_model=Dict)
async def update_status(
    bot_id: str,
    status: BotStatus,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Update bot status (Admin only)."""
    context = {
        "bot_id": bot_id,
        "status": status,
        "user_id": str(current_user.id)
    }
    
    try:
        await reference_manager.update_reference(
            source_type="Bot",
            reference_id=bot_id,
            updates={
                "status": status,
                "modified_at": datetime.utcnow()
            },
            validate=True
        )

        # Handle WebSocket connections based on status
        if status == BotStatus.ACTIVE:
            await ws_manager.setup_bot_connections(bot_id)
        elif status == BotStatus.STOPPED:
            await ws_manager.cleanup_bot_connections(bot_id)

        logger.info(
            "Updated bot status",
            extra=context
        )

        return {
            "success": True,
            "status": status
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Status update failed"
        )

@router.post("/{bot_id}/connect-account", response_model=Dict)
async def connect_account(
    bot_id: str,
    account_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Connect account to bot (Admin only)."""
    context = {
        "bot_id": bot_id,
        "account_id": account_id,
        "user_id": str(current_user.id)
    }
    
    try:
        # Validate references exist
        await reference_manager.validate_references(
            source_type="Bot",
            references={
                "bot": bot_id,
                "account": account_id
            }
        )

        # Connect via service
        await trading_service.connect_bot_account(
            bot_id=bot_id,
            account_id=account_id
        )

        logger.info(
            "Connected account to bot",
            extra=context
        )

        return {
            "success": True,
            "message": "Account connected"
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Account connection failed"
        )

@router.post("/{bot_id}/disconnect-account", response_model=Dict)
async def disconnect_account(
    bot_id: str,
    account_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Disconnect account from bot (Admin only)."""
    context = {
        "bot_id": bot_id,
        "account_id": account_id,
        "user_id": str(current_user.id)
    }
    
    try:
        await trading_service.disconnect_bot_account(
            bot_id=bot_id,
            account_id=account_id
        )

        logger.info(
            "Disconnected account from bot",
            extra=context
        )

        return {
            "success": True,
            "message": "Account disconnected"
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Account disconnection failed"
        )

@router.post("/{bot_id}/terminate", response_model=Dict)
async def terminate_bot(
    bot_id: str,
    current_user: User = Depends(get_admin_user)
) -> Dict:
    """Terminate all positions and orders for bot accounts (Admin only)."""
    context = {
        "bot_id": bot_id,
        "user_id": str(current_user.id)
    }
    
    try:
        # Terminate via exchange operations
        result = await ExchangeOperations.terminate_bot_accounts(
            bot_id=bot_id,
            reference_manager=reference_manager
        )

        if result["success"]:
            # Update bot status
            await reference_manager.update_reference(
                source_type="Bot",
                reference_id=bot_id,
                updates={
                    "status": BotStatus.STOPPED,
                    "modified_at": datetime.utcnow()
                }
            )

        logger.info(
            "Bot terminated",
            extra={
                **context,
                "terminated_accounts": result.get("terminated_accounts", 0)
            }
        )

        return result

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Bot termination failed"
        )

@router.get("/{bot_id}/accounts", response_model=List[Dict])
async def get_connected_accounts(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_viewable_bots)
) -> List[Dict]:
    """Get accounts connected to bot."""
    context = {
        "bot_id": bot_id,
        "user_id": str(current_user.id)
    }
    
    try:
        if bot_id not in viewable_bots:
            raise AuthorizationError(
                "Not authorized to view this bot",
                context=context
            )

        # Get accounts via reference manager 
        accounts = await reference_manager.get_references(
            source_type="Bot",
            reference_id=bot_id,
            target_type="Account"
        )

        logger.info(
            "Retrieved connected accounts",
            extra={
                **context,
                "account_count": len(accounts)
            }
        )

        return accounts

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get connected accounts"
        )

@router.get("/{bot_id}/performance", response_model=Dict)
async def get_bot_performance(
    bot_id: str,
    current_user: User = Depends(get_current_active_user),
    viewable_bots: List[str] = Depends(get_viewable_bots)
) -> Dict:
    """Get bot performance metrics."""
    context = {
        "bot_id": bot_id,
        "user_id": str(current_user.id)
    }
    
    try:
        if bot_id not in viewable_bots:
            raise AuthorizationError(
                "Not authorized to view this bot",
                context=context
            )

        metrics = await performance_service.get_bot_metrics(
            bot_id=bot_id,
            include_accounts=True
        )

        logger.info(
            "Retrieved bot performance",
            extra={
                **context,
                "metrics": list(metrics.keys())
            }
        )

        return metrics

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get bot performance"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
    DatabaseError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import TimeFrame, BotStatus

from app.models.user import User
from app.services.exchange.operations import ExchangeOperations 
from app.services.reference.manager import reference_manager
from app.services.trading.service import trading_service
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager

logger = get_logger(__name__)