"""
Account group management endpoints with standardized error handling.

This module provides HTTP endpoints for managing account groups,
consistently using the CRUD layer for database operations and service integrations.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from beanie import PydanticObjectId

from fastapi import APIRouter, Depends, Query, Path, Request, status
from pydantic import BaseModel, Field

# Core imports
from app.core.errors.base import ValidationError, NotFoundError, AuthorizationError
from app.core.logging.logger import get_logger
from app.api.v1.references import ServiceResponse

# Import models, dependencies, and services
from app.crud.crud_group import group as group_crud, GroupCreate, GroupUpdate
from app.api.v1.deps import get_current_user, get_admin_user

router = APIRouter()
logger = get_logger(__name__)


# --- Request Models ---
class CreateGroupRequest(BaseModel):
    """Request model for group creation."""
    name: str = Field(..., min_length=3, max_length=32)
    description: Optional[str] = None
    accounts: List[str] = Field(default_factory=list)


class UpdateGroupRequest(BaseModel):
    """Request model for group updates."""
    description: Optional[str] = None
    accounts: Optional[List[str]] = None


class BulkAccountsRequest(BaseModel):
    """Request model for bulk account operations."""
    account_ids: List[str] = Field(..., min_items=1)


# --- Helper Functions ---
async def get_object_id(id_str: str) -> PydanticObjectId:
    """Convert string ID to PydanticObjectId for database operations."""
    try:
        return PydanticObjectId(id_str)
    except Exception:
        raise ValidationError(
            "Invalid ID format",
            context={"id": id_str}
        )


async def validate_group_access(group_id: str, current_user: Dict) -> None:
    """
    Ensure the current user has access to the group using the CRUD layer.
    Raises AuthorizationError if access is denied.
    """
    obj_id = await get_object_id(group_id)
    has_access = await group_crud.validate_user_access(
        group_id=obj_id,
        user_id=str(current_user.get("id"))
    )
    
    if not has_access:
        raise AuthorizationError(
            "Not authorized to access group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )


# --- Endpoints ---

@router.get("/")
async def list_groups(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """List accessible account groups."""
    context = {"user_id": str(current_user.get("id"))}
    
    # Use the appropriate query based on user role
    # Note: Access control is handled in the db query, not in memory filtering
    query = {}
    if current_user.get("role") != "admin":  # Using string to avoid importing UserRole enum
        # Get groups assigned to this user
        assigned_groups = await group_crud.get_assigned_groups(
            user_id=str(current_user.get("id"))
        )
        
        if not assigned_groups:
            return ServiceResponse(
                success=True,
                message="No groups found",
                data={"groups": [], "total": 0, "offset": offset, "limit": limit}
            )
        
        group_ids = [g.id for g in assigned_groups]
        query["_id"] = {"$in": group_ids}
    
    # Get groups with pagination
    groups = await group_crud.get_multi(
        skip=offset,
        limit=limit,
        query=query
    )
    
    # Get total count for pagination
    total = await group_crud.model.find(query).count()
    
    logger.info("Listed groups", extra={**context, "count": len(groups)})
    
    return ServiceResponse(
        success=True,
        message="Groups retrieved successfully",
        data={
            "groups": [g.to_dict() for g in groups],
            "total": total,
            "offset": offset,
            "limit": limit
        }
    )


@router.get("/{group_id}")
async def get_group(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get detailed group information."""
    context = {"group_id": group_id, "user_id": str(current_user.get("id"))}
    
    # Validate group access
    await validate_group_access(group_id, current_user)
    
    # Get group with object ID
    obj_id = await get_object_id(group_id)
    group = await group_crud.get(obj_id)
    
    # Enrich with additional data
    group_data = group.to_dict()
    
    # Add WebSocket and performance data if available
    try:
        ws_status = await group_crud.verify_websocket_health(obj_id)
        group_data["websocket_status"] = ws_status
    except Exception as e:
        logger.warning(
            "Failed to get WebSocket status",
            extra={**context, "error": str(e)}
        )
    
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        performance = await group_crud.get_period_stats(obj_id, today, today)
        group_data["performance"] = performance
    except Exception as e:
        logger.warning(
            "Failed to get performance data",
            extra={**context, "error": str(e)}
        )
    
    logger.info("Retrieved group details", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Group retrieved successfully",
        data={"group": group_data}
    )


@router.post("/")
async def create_group(
    request: Request,
    data: CreateGroupRequest,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Create a new account group (Admin only)."""
    context = {
        "name": data.name,
        "account_count": len(data.accounts),
        "user_id": str(current_user.get("id"))
    }
    
    # Create group using CRUD operation
    group_in = GroupCreate(
        name=data.name,
        description=data.description,
        accounts=data.accounts
    )
    
    group = await group_crud.create(group_in)
    
    logger.info("Created group", extra={**context, "group_id": str(group.id)})
    
    return ServiceResponse(
        success=True,
        message="Group created successfully",
        data={"group": group.to_dict()}
    )


@router.patch("/{group_id}")
async def update_group(
    request: Request,
    data: UpdateGroupRequest,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Update group settings (Admin only)."""
    context = {
        "group_id": group_id,
        "fields": list(data.model_dump(exclude_unset=True).keys()),
        "user_id": str(current_user.get("id"))
    }
    
    # Update group using CRUD operation
    obj_id = await get_object_id(group_id)
    group_in = GroupUpdate(**data.model_dump(exclude_unset=True))
    
    group = await group_crud.update(obj_id, group_in)
    
    logger.info("Updated group", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Group updated successfully",
        data={"group": group.to_dict()}
    )


@router.get("/{group_id}/performance")
async def get_group_performance(
    request: Request,
    group_id: str = Path(...),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get group performance metrics."""
    context = {"group_id": group_id, "date_range": f"{start_date} to {end_date}"}
    
    # Validate group access
    await validate_group_access(group_id, current_user)
    
    # Get performance data
    obj_id = await get_object_id(group_id)
    performance = await group_crud.get_performance(obj_id, start_date, end_date)
    
    logger.info("Retrieved group performance", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Performance data retrieved",
        data={"performance": performance}
    )


@router.get("/{group_id}/metrics")
async def get_group_metrics(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get current group metrics and health status."""
    context = {"group_id": group_id, "user_id": str(current_user.get("id"))}
    
    # Validate group access
    await validate_group_access(group_id, current_user)
    
    # Get metrics data
    obj_id = await get_object_id(group_id)
    websocket_health = await group_crud.verify_websocket_health(obj_id)
    balance_status = await group_crud.sync_balances(obj_id)
    
    logger.info("Retrieved group metrics", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Group metrics retrieved",
        data={
            "websocket_health": websocket_health,
            "balance_status": balance_status
        }
    )


@router.post("/{group_id}/accounts")
async def bulk_add_accounts(
    request: Request,
    data: BulkAccountsRequest,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Add multiple accounts to a group (Admin only)."""
    context = {
        "group_id": group_id,
        "account_count": len(data.account_ids),
        "user_id": str(current_user.get("id"))
    }
    
    # Add accounts using CRUD operation
    obj_id = await get_object_id(group_id)
    
    # Handle each account separately to capture success/failure
    results = {"success": [], "failed": []}
    for account_id in data.account_ids:
        try:
            await group_crud.add_account(obj_id, account_id)
            results["success"].append(account_id)
        except Exception as e:
            results["failed"].append({
                "account_id": account_id,
                "error": str(e)
            })
    
    # Get updated group
    group = await group_crud.get(obj_id)
    
    logger.info(
        "Bulk added accounts to group",
        extra={
            **context,
            "success_count": len(results["success"]),
            "failed_count": len(results["failed"])
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Bulk account addition completed",
        data={"results": results, "group": group.to_dict()}
    )


@router.post("/{group_id}/accounts/{account_id}")
async def add_account(
    request: Request,
    group_id: str = Path(...),
    account_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Add a single account to a group (Admin only)."""
    context = {
        "group_id": group_id,
        "account_id": account_id,
        "user_id": str(current_user.get("id"))
    }
    
    # Add account using CRUD operation
    obj_id = await get_object_id(group_id)
    group = await group_crud.add_account(obj_id, account_id)
    
    logger.info("Added account to group", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Account added to group",
        data={"group": group.to_dict()}
    )


@router.delete("/{group_id}/accounts/{account_id}")
async def remove_account(
    request: Request,
    group_id: str = Path(...),
    account_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Remove an account from a group (Admin only)."""
    context = {
        "group_id": group_id,
        "account_id": account_id,
        "user_id": str(current_user.get("id"))
    }
    
    # Remove account using CRUD operation
    obj_id = await get_object_id(group_id)
    group = await group_crud.remove_account(obj_id, account_id)
    
    logger.info("Removed account from group", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Account removed from group",
        data={"group": group.to_dict()}
    )


@router.delete("/{group_id}")
async def delete_group(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Delete a group (Admin only)."""
    context = {"group_id": group_id, "user_id": str(current_user.get("id"))}
    
    # Delete group using CRUD operation
    obj_id = await get_object_id(group_id)
    await group_crud.delete(obj_id)
    
    logger.info("Deleted group", extra=context)
    
    return ServiceResponse(
        success=True,
        message="Group deleted successfully",
        data={"group_id": group_id}
    )
