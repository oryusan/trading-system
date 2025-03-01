"""
Account management endpoints with standardized error handling and service integration.

Features:
- CRUD operations for trading accounts
- Consistent error handling
- Standardized response formats
- Clear separation of HTTP concerns from business logic
"""

from datetime import datetime, timedelta
import asyncio
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Path, status
from fastapi.responses import JSONResponse
from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.config.exchange_metadata import get_all_exchanges
from app.core.errors.base import AuthorizationError, NotFoundError, ValidationError, ExchangeError
from app.core.logging.logger import get_logger
from app.api.v1.deps import get_admin_user, get_current_user, get_accessible_accounts
from app.api.v1.references import ServiceResponse
from app.crud.crud_account import account as account_crud, AccountCreate, AccountUpdate

router = APIRouter()
logger = get_logger(__name__)


# --- Pydantic models for request bodies ---
class AccountCreateRequest(BaseModel):
    """Request model for account creation."""
    exchange: str
    api_key: str
    api_secret: str
    name: str
    initial_balance: float
    passphrase: Optional[str] = None
    is_testnet: bool = False
    bot_id: Optional[str] = None
    group_ids: List[str] = Field(default_factory=list)

class AccountUpdateCredentialsRequest(BaseModel):
    """Request model for updating account credentials."""
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None

class AccountAssignGroupRequest(BaseModel):
    """Request model for assigning an account to a group."""
    group_id: str


# --- Helper function ---
def check_account_access(account_id: str, allowed_accounts: List[str]) -> None:
    """Verify that the user has access to the specified account."""
    if account_id not in allowed_accounts:
        raise AuthorizationError(
            "Not authorized to access this account", 
            context={"account_id": account_id}
        )


# --- Endpoints ---

@router.post("/create", response_model=ServiceResponse)
async def create_account(
    request: Request,
    account_data: AccountCreateRequest,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new trading account.
    Admin only.
    """
    # Map the request data to the CRUD input model
    crud_input = AccountCreate(
        user_id=str(current_user["id"]),
        exchange=account_data.exchange,
        api_key=account_data.api_key,
        api_secret=account_data.api_secret,
        passphrase=account_data.passphrase,
        name=account_data.name,
        initial_balance=account_data.initial_balance,
        is_testnet=account_data.is_testnet,
        bot_id=account_data.bot_id,
        group_ids=account_data.group_ids
    )
    
    # Create account using CRUD layer
    account = await account_crud.create(crud_input)
    
    logger.info(
        "Account created successfully", 
        extra={
            "account_id": str(account.id),
            "exchange": account_data.exchange,
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account created successfully",
        data={
            "account_id": str(account.id),
            "name": account.name,
            "exchange": account.exchange.value
        }
    )

@router.get("/list", response_model=ServiceResponse)
async def list_accounts(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    List accounts accessible to the current user.
    """
    # Convert string IDs to ObjectIDs for query
    account_ids = [PydanticObjectId(id) for id in allowed_accounts]
    
    # Get accounts with id in allowed_accounts
    query = {"_id": {"$in": account_ids}}
    accounts = await account_crud.get_multi(skip=skip, limit=limit, query=query)
    total = len(allowed_accounts)
    
    # Prepare enriched accounts with additional data
    enriched_accounts = []
    for acc in accounts:
        # Get performance data
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        try:
            metrics = await account_crud.get_performance(
                account_id=acc.id,
                start_date=yesterday,
                end_date=now
            )
        except Exception as e:
            metrics = {"error": str(e)}
        
        # Add enriched account data
        enriched_accounts.append({
            "account": acc.to_dict(),
            "performance": metrics
        })
    
    return ServiceResponse(
        success=True,
        message=f"Retrieved {len(accounts)} accounts",
        data={
            "accounts": enriched_accounts,
            "pagination": {
                "total": total,
                "skip": skip,
                "limit": limit
            }
        }
    )

@router.get("/{account_id}", response_model=ServiceResponse)
async def get_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Get detailed account information.
    User must have access to the account.
    """
    # Verify access
    check_account_access(account_id, allowed_accounts)
    
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Get account via CRUD layer
    account = await account_crud.get(obj_id)
    
    # Get additional data for account
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    
    try:
        metrics = await account_crud.get_performance(
            account_id=obj_id,
            start_date=yesterday,
            end_date=now
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch account performance metrics",
            extra={"account_id": account_id, "error": str(e)}
        )
        metrics = {"error": str(e)}
    
    # Check trade limits
    try:
        trade_limits = await account_crud.check_trade_limits(obj_id)
    except Exception as e:
        logger.warning(
            "Failed to check account trade limits",
            extra={"account_id": account_id, "error": str(e)}
        )
        trade_limits = {"error": str(e)}
    
    # Get WebSocket status
    from app.services.websocket.manager import ws_manager
    try:
        ws_status = await ws_manager.get_connection_status(account_id)
    except Exception as e:
        logger.warning(
            "Failed to fetch WebSocket status",
            extra={"account_id": account_id, "error": str(e)}
        )
        ws_status = {"connected": False, "error": str(e)}
    
    return ServiceResponse(
        success=True,
        message="Account retrieved successfully",
        data={
            "account": account.to_dict(),
            "performance": metrics,
            "trade_limits": trade_limits,
            "ws_status": ws_status
        }
    )

@router.post("/{account_id}/update-credentials", response_model=ServiceResponse)
async def update_credentials(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    credentials: AccountUpdateCredentialsRequest = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update account credentials.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Update via CRUD layer
    await account_crud.update(
        id=obj_id,
        obj_in={
            "api_key": credentials.api_key,
            "api_secret": credentials.api_secret,
            "passphrase": credentials.passphrase
        }
    )
    
    logger.info(
        "Updated account credentials",
        extra={"account_id": account_id, "user_id": str(current_user["id"])}
    )
    
    return ServiceResponse(
        success=True,
        message="Account credentials updated successfully"
    )

@router.post("/{account_id}/sync-balance", response_model=ServiceResponse)
async def sync_balance(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Synchronize account balance with exchange.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Sync balance via CRUD layer
    result = await account_crud.sync_balance(obj_id)
    
    logger.info(
        "Synchronized account balance",
        extra={
            "account_id": account_id,
            "balance": result.get("balance"),
            "equity": result.get("equity"),
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account balance synchronized successfully",
        data=result
    )

@router.get("/{account_id}/performance", response_model=ServiceResponse)
async def get_performance(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    start_date: datetime = Query(None, description="Start date"),
    end_date: datetime = Query(None, description="End date"),
    current_user: Dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Get account performance metrics.
    User must have access to the account.
    """
    # Verify access
    check_account_access(account_id, allowed_accounts)
    
    # Set default dates if not provided
    if not end_date:
        end_date = datetime.utcnow()
    if not start_date:
        start_date = end_date - timedelta(days=7)
    
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Get performance via CRUD layer
    metrics = await account_crud.get_performance(
        account_id=obj_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return ServiceResponse(
        success=True,
        message="Performance metrics retrieved successfully",
        data={
            "account_id": account_id,
            "time_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "metrics": metrics
        }
    )

@router.delete("/{account_id}", response_model=ServiceResponse)
async def delete_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Delete an account.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Delete account via CRUD layer
    success = await account_crud.delete(obj_id)
    
    logger.info(
        "Deleted account",
        extra={"account_id": account_id, "user_id": str(current_user["id"])}
    )
    
    return ServiceResponse(
        success=True,
        message="Account deleted successfully"
    )

@router.post("/{account_id}/assign-group", response_model=ServiceResponse)
async def assign_group(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    assignment: AccountAssignGroupRequest = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Assign an account to a group.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Assign to group via CRUD layer
    updated_account = await account_crud.add_to_group(
        account_id=obj_id,
        group_id=assignment.group_id
    )
    
    logger.info(
        "Assigned account to group",
        extra={
            "account_id": account_id,
            "group_id": assignment.group_id,
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account assigned to group successfully",
        data={"account": updated_account.to_dict()}
    )

@router.delete("/{account_id}/remove-group/{group_id}", response_model=ServiceResponse)
async def remove_from_group(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Remove an account from a group.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Remove from group via CRUD layer
    updated_account = await account_crud.remove_from_group(
        account_id=obj_id,
        group_id=group_id
    )
    
    logger.info(
        "Removed account from group",
        extra={
            "account_id": account_id,
            "group_id": group_id,
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account removed from group successfully",
        data={"account": updated_account.to_dict()}
    )

@router.post("/{account_id}/assign-bot/{bot_id}", response_model=ServiceResponse)
async def assign_bot(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    bot_id: str = Path(..., description="Bot ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Assign an account to a bot.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Assign to bot via CRUD layer
    updated_account = await account_crud.assign_to_bot(
        account_id=obj_id,
        bot_id=bot_id
    )
    
    logger.info(
        "Assigned account to bot",
        extra={
            "account_id": account_id,
            "bot_id": bot_id,
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account assigned to bot successfully",
        data={"account": updated_account.to_dict()}
    )

@router.post("/{account_id}/unassign-bot", response_model=ServiceResponse)
async def unassign_bot(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Remove a bot assignment from an account.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Unassign from bot via CRUD layer
    updated_account = await account_crud.unassign_from_bot(
        account_id=obj_id
    )
    
    logger.info(
        "Unassigned account from bot",
        extra={
            "account_id": account_id,
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account unassigned from bot successfully",
        data={"account": updated_account.to_dict()}
    )

@router.post("/{account_id}/validate-credentials", response_model=ServiceResponse)
async def validate_credentials(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Validate account credentials with the exchange.
    Admin only.
    """
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Validate credentials via CRUD layer
    result = await account_crud.validate_credentials(obj_id)
    
    logger.info(
        "Validated account credentials",
        extra={
            "account_id": account_id,
            "valid": result.get("valid", False),
            "user_id": str(current_user["id"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account credentials validated successfully",
        data=result
    )

@router.get("/{account_id}/check-limits", response_model=ServiceResponse)
async def check_trade_limits(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> ServiceResponse:
    """
    Check trading limits for an account.
    User must have access to the account.
    """
    # Verify access
    check_account_access(account_id, allowed_accounts)
    
    # Convert string ID to ObjectID
    obj_id = PydanticObjectId(account_id)
    
    # Check limits via CRUD layer
    limits = await account_crud.check_trade_limits(obj_id)
    
    return ServiceResponse(
        success=True,
        message="Trade limits checked successfully",
        data={
            "account_id": account_id,
            "limits": limits,
            "can_trade": limits.get("can_trade", False)
        }
    )

@router.get("/exchanges", response_model=ServiceResponse)
async def get_exchanges(
    request: Request,
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get list of supported exchanges with their requirements.
    """
    return ServiceResponse(
        success=True,
        message="Exchanges retrieved successfully",
        data={"exchanges": get_all_exchanges()}
    )