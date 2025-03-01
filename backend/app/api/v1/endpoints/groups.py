"""
Account group management endpoints with standardized error handling and group-based access control.

This module provides HTTP endpoints for group management operations including:
- Group CRUD operations
- Account assignments within groups
- Group performance metrics
- Export functionality (for ADMIN and EXPORTER roles)
"""

from datetime import datetime
from typing import List, Dict, Any, Optional, Union
import io
from fastapi import APIRouter, Depends, Query, Path, Request, status
from fastapi.responses import StreamingResponse
from beanie import PydanticObjectId

from app.crud.crud_group import group as group_crud, GroupCreate, GroupUpdate
from app.crud.crud_user import user as user_crud
from app.core.references import UserRole
from app.core.errors.base import ValidationError, NotFoundError, AuthorizationError
from app.core.logging.logger import get_logger
from app.api.v1.deps import get_admin_user, get_current_user
from app.api.v1.references import ServiceResponse

router = APIRouter()
logger = get_logger(__name__)


@router.get("/")
async def list_groups(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    List groups accessible to the current user.
    
    - ADMIN users see all groups
    - Other users see only their assigned groups
    """
    # Get groups assigned to the user
    assigned_groups = await user_crud.get_assigned_groups(str(current_user.get("id")))
    
    # Build response
    total = len(assigned_groups)
    paginated_groups = assigned_groups[offset:offset+limit]
    
    logger.info(
        "Listed groups", 
        extra={
            "user_id": str(current_user.get("id")),
            "count": len(paginated_groups),
            "total": total
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Groups retrieved successfully",
        data={
            "groups": paginated_groups,
            "pagination": {
                "total": total,
                "offset": offset,
                "limit": limit
            }
        }
    )


@router.get("/{group_id}")
async def get_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get detailed group information.
    User must have access to the group.
    """
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get group details
    obj_id = PydanticObjectId(group_id)
    group = await group_crud.get(obj_id)
    
    # Get WebSocket and performance data
    try:
        ws_status = await group_crud.verify_websocket_health(obj_id)
        performance = await group_crud.get_current_metrics(obj_id)
    except Exception as e:
        logger.warning(
            "Error fetching additional group data",
            extra={"group_id": group_id, "error": str(e)}
        )
        ws_status = {"error": "Failed to fetch WebSocket status"}
        performance = {"error": "Failed to fetch performance metrics"}
        
    logger.info(
        "Retrieved group details", 
        extra={
            "group_id": group_id,
            "user_id": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group retrieved successfully",
        data={
            "group": group.to_dict(),
            "websocket_status": ws_status,
            "performance": performance
        }
    )


@router.post("/")
async def create_group(
    request: Request,
    group_data: GroupCreate,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new account group.
    Admin only.
    """
    # Create group via CRUD service
    new_group = await group_crud.create(group_data)
    
    logger.info(
        "Created new group",
        extra={
            "group_id": str(new_group.id),
            "name": new_group.name,
            "created_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group created successfully",
        data={"group": new_group.to_dict()}
    )


@router.patch("/{group_id}")
async def update_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    group_data: GroupUpdate = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update an existing group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Update group via CRUD service
    updated_group = await group_crud.update(obj_id, group_data)
    
    logger.info(
        "Updated group",
        extra={
            "group_id": group_id,
            "fields": list(group_data.model_dump(exclude_unset=True).keys()),
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group updated successfully",
        data={"group": updated_group.to_dict()}
    )


@router.get("/{group_id}/accounts")
async def get_group_accounts(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get accounts in a group.
    User must have access to the group.
    """
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get group and its accounts
    obj_id = PydanticObjectId(group_id)
    accounts = await group_crud.get_group_accounts(obj_id)
    
    logger.info(
        "Retrieved group accounts", 
        extra={
            "group_id": group_id,
            "user_id": str(current_user.get("id")),
            "account_count": len(accounts)
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group accounts retrieved successfully",
        data={"accounts": accounts}
    )


@router.post("/{group_id}/accounts")
async def add_accounts_to_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    account_ids: List[str] = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Add multiple accounts to a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Add accounts to group via CRUD service
    updated_group = await group_crud.add_accounts(obj_id, account_ids)
    
    logger.info(
        "Added accounts to group",
        extra={
            "group_id": group_id,
            "account_count": len(account_ids),
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Accounts added to group successfully",
        data={"group": updated_group.to_dict()}
    )


@router.delete("/{group_id}/accounts")
async def remove_accounts_from_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    account_ids: List[str] = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Remove multiple accounts from a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Remove accounts from group via CRUD service
    updated_group = await group_crud.remove_accounts(obj_id, account_ids)
    
    logger.info(
        "Removed accounts from group",
        extra={
            "group_id": group_id,
            "account_count": len(account_ids),
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Accounts removed from group successfully",
        data={"group": updated_group.to_dict()}
    )


@router.post("/{group_id}/accounts/{account_id}")
async def add_account_to_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Add a single account to a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Add account to group via CRUD service
    updated_group = await group_crud.add_account(obj_id, account_id)
    
    logger.info(
        "Added account to group",
        extra={
            "group_id": group_id,
            "account_id": account_id,
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account added to group successfully",
        data={"group": updated_group.to_dict()}
    )


@router.delete("/{group_id}/accounts/{account_id}")
async def remove_account_from_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Remove a single account from a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Remove account from group via CRUD service
    updated_group = await group_crud.remove_account(obj_id, account_id)
    
    logger.info(
        "Removed account from group",
        extra={
            "group_id": group_id,
            "account_id": account_id,
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Account removed from group successfully",
        data={"group": updated_group.to_dict()}
    )


@router.get("/{group_id}/performance")
async def get_group_performance(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    period: str = Query("daily", description="Period (daily, weekly, monthly)"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get performance metrics for a group.
    User must have access to the group.
    """
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get performance data
    obj_id = PydanticObjectId(group_id)
    performance = await group_crud.get_performance(
        obj_id, 
        start_date=start_date,
        end_date=end_date,
        period=period
    )
    
    logger.info(
        "Retrieved group performance", 
        extra={
            "group_id": group_id,
            "user_id": str(current_user.get("id")),
            "period": period,
            "date_range": f"{start_date or 'all'} to {end_date or 'current'}"
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group performance retrieved successfully",
        data={
            "group_id": group_id,
            "performance": performance,
            "period": period,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
    )


@router.get("/{group_id}/metrics")
async def get_group_metrics(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get current metrics for a group (balances, counts, status).
    User must have access to the group.
    """
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get metrics data
    obj_id = PydanticObjectId(group_id)
    metrics = await group_crud.get_current_metrics(obj_id)
    
    logger.info(
        "Retrieved group metrics", 
        extra={
            "group_id": group_id,
            "user_id": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group metrics retrieved successfully",
        data={
            "group_id": group_id,
            "metrics": metrics
        }
    )


@router.delete("/{group_id}")
async def delete_group(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Delete a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Get group name for logging
    group = await group_crud.get(obj_id)
    group_name = group.name
    
    # Delete group via CRUD service
    await group_crud.delete(obj_id)
    
    logger.info(
        "Deleted group", 
        extra={
            "group_id": group_id,
            "group_name": group_name,
            "deleted_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group deleted successfully",
        data={"group_id": group_id}
    )


@router.get("/{group_id}/sync")
async def sync_group_balances(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Synchronize account balances for all accounts in a group.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(group_id)
    
    # Sync balances via CRUD service
    sync_results = await group_crud.sync_balances(obj_id)
    
    logger.info(
        "Synced group balances", 
        extra={
            "group_id": group_id,
            "success_count": len([r for r in sync_results.get("results", []) if r.get("success")]),
            "error_count": len([r for r in sync_results.get("results", []) if not r.get("success")]),
            "synced_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Group balances synced successfully",
        data=sync_results
    )


@router.post("/group/{group_id}/terminate")
async def terminate_group_positions(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    current_user: Any = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Terminate all positions for all accounts in a group.
    Admin only.
    """
    context = get_request_context(request, group_id=group_id, user_id=str(current_user.user_id))
    logger.info("Processing group position termination request", extra=context)
    
    # Get group accounts
    from app.crud.crud_group import group as group_crud
    group = await group_crud.get(PydanticObjectId(group_id))
    
    if not group.accounts:
        return ServiceResponse(
            success=True,
            message="No accounts in group to terminate",
            data={"group_id": group_id, "accounts_count": 0}
        )
    
    # Track results for each account
    results = []
    success_count = 0
    
    # Process each account
    for account_id in group.accounts:
        try:
            # Terminate positions for this account
            from app.services.trading.service import trading_service
            termination_result = await trading_service.terminate_account(account_id=account_id)
            
            # Record result
            results.append({
                "account_id": account_id,
                "success": termination_result.get("success", False),
                "positions_terminated": termination_result.get("positions_terminated", 0)
            })
            
            if termination_result.get("success", False):
                success_count += 1
                
        except Exception as e:
            logger.error(
                f"Failed to terminate positions for account {account_id}",
                extra={**context, "account_id": account_id, "error": str(e)}
            )
            results.append({
                "account_id": account_id,
                "success": False,
                "error": str(e)
            })
    
    return ServiceResponse(
        success=success_count > 0,
        message=f"Terminated positions for {success_count} of {len(group.accounts)} accounts",
        data={
            "group_id": group_id,
            "accounts_processed": len(group.accounts),
            "success_count": success_count,
            "results": results
        }
    )


# ----- EXPORT ENDPOINTS (ADMIN & EXPORTER ONLY) -----

@router.get("/{group_id}/export", response_class=StreamingResponse)
async def export_group_data(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    format: str = Query("csv", description="Export format (csv or xlsx)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: Dict = Depends(get_current_user)
) -> StreamingResponse:
    """
    Export consolidated data for all accounts in a group.
    Only available to ADMIN and EXPORTER roles.
    """
    # Check if user has EXPORTER role
    if current_user.get("role") != UserRole.ADMIN.value and current_user.get("role") != UserRole.EXPORTER.value:
        raise AuthorizationError(
            "Export operations require EXPORTER role",
            context={"user_id": str(current_user.get("id")), "role": current_user.get("role")}
        )
        
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get group for filename
    obj_id = PydanticObjectId(group_id)
    group = await group_crud.get(obj_id)
    
    # Generate export file using group CRUD service
    buffer, filename = await group_crud.export_group_data(
        group_id=obj_id,
        format=format,
        start_date=start_date,
        end_date=end_date
    )
    
    # Log the export
    logger.info(
        "Exported group data",
        extra={
            "user_id": str(current_user.get("id")),
            "group_id": group_id,
            "format": format,
            "date_range": f"{start_date or 'all'} to {end_date or 'current'}"
        }
    )
    
    # Return as downloadable file
    media_type = "text/csv" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{group_id}/export-trades", response_class=StreamingResponse)
async def export_group_trades(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    format: str = Query("csv", description="Export format (csv or xlsx)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: Dict = Depends(get_current_user)
) -> StreamingResponse:
    """
    Export trade history for all accounts in a group.
    Only available to ADMIN and EXPORTER roles.
    """
    # Check if user has EXPORTER role
    if current_user.get("role") != UserRole.ADMIN.value and current_user.get("role") != UserRole.EXPORTER.value:
        raise AuthorizationError(
            "Export operations require EXPORTER role",
            context={"user_id": str(current_user.get("id")), "role": current_user.get("role")}
        )
        
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get group for filename
    obj_id = PydanticObjectId(group_id)
    group = await group_crud.get(obj_id)
    
    # Generate export file using group CRUD service
    buffer, filename = await group_crud.export_group_trades(
        group_id=obj_id,
        format=format,
        start_date=start_date,
        end_date=end_date
    )
    
    # Log the export
    logger.info(
        "Exported group trades",
        extra={
            "user_id": str(current_user.get("id")),
            "group_id": group_id,
            "format": format,
            "date_range": f"{start_date or 'all'} to {end_date or 'current'}"
        }
    )
    
    # Return as downloadable file
    media_type = "text/csv" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{group_id}/export-performance", response_class=StreamingResponse)
async def export_group_performance(
    request: Request,
    group_id: str = Path(..., description="Group ID"),
    format: str = Query("csv", description="Export format (csv or xlsx)"),
    period: str = Query("daily", description="Performance period (daily, weekly, monthly)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: Dict = Depends(get_current_user)
) -> StreamingResponse:
    """
    Export performance metrics for a group.
    Only available to ADMIN and EXPORTER roles.
    """
    # Check if user has EXPORTER role
    if current_user.get("role") != UserRole.ADMIN.value and current_user.get("role") != UserRole.EXPORTER.value:
        raise AuthorizationError(
            "Export operations require EXPORTER role",
            context={"user_id": str(current_user.get("id")), "role": current_user.get("role")}
        )
        
    # Check if user has access to the group
    if not await user_crud.check_group_access(str(current_user.get("id")), group_id):
        raise AuthorizationError(
            "Not authorized to access this group",
            context={"user_id": str(current_user.get("id")), "group_id": group_id}
        )
    
    # Get group for filename
    obj_id = PydanticObjectId(group_id)
    group = await group_crud.get(obj_id)
    
    # Generate export file using group CRUD service
    buffer, filename = await group_crud.export_group_performance(
        group_id=obj_id,
        period=period,
        format=format,
        start_date=start_date,
        end_date=end_date
    )
    
    # Log the export
    logger.info(
        "Exported group performance",
        extra={
            "user_id": str(current_user.get("id")),
            "group_id": group_id,
            "period": period,
            "format": format,
            "date_range": f"{start_date or 'all'} to {end_date or 'current'}"
        }
    )
    
    # Return as downloadable file
    media_type = "text/csv" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )