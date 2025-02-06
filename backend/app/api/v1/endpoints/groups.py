"""
Account group management endpoints with enhanced error handling.

Features:
- Group CRUD operations
- Performance tracking
- Account management
- Enhanced error handling
- Service integration 
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Path, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

# Core imports
from app.core.errors import (
    ValidationError,
    DatabaseError, 
    NotFoundError,
    AuthorizationError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    UserRole,
    DateRange,
    ServiceResponse
)

# Request Models
class CreateGroupRequest(BaseModel):
    """Request model for group creation."""
    name: str = Field(..., min_length=3, max_length=32)
    description: Optional[str] = None
    accounts: List[str] = Field(default_factory=list)
    max_drawdown: float = Field(25.0, gt=0, le=100)
    target_monthly_roi: float = Field(5.0, gt=0)
    risk_limit: float = Field(5.0, gt=0, le=100)

class UpdateGroupRequest(BaseModel):
    """Request model for group updates."""
    description: Optional[str] = None
    accounts: Optional[List[str]] = None
    max_drawdown: Optional[float] = Field(None, gt=0, le=100)
    target_monthly_roi: Optional[float] = Field(None, gt=0)
    risk_limit: Optional[float] = Field(None, gt=0, le=100)

class BulkAccountsRequest(BaseModel):
    """Request model for bulk account operations."""
    account_ids: List[str] = Field(..., min_items=1)

router = APIRouter()
logger = get_logger(__name__)

@router.get("/")
async def list_groups(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """List accessible account groups."""
    try:
        query = {}
        
        # Non-admins can only see assigned groups
        if current_user.get("role") != UserRole.ADMIN:
            assigned_groups = await reference_manager.get_references(
                source_type="User",
                reference_id=str(current_user.get("id"))
            )
            if not assigned_groups:
                return ServiceResponse(
                    success=True,
                    message="No groups found",
                    data={"groups": []}
                )
            query["_id"] = {"$in": [g.get("id") for g in assigned_groups]}

        groups = await AccountGroup.find(query).skip(offset).limit(limit).to_list()
        
        return ServiceResponse(
            success=True,
            message="Groups retrieved successfully",
            data={
                "groups": [g.to_dict() for g in groups],
                "total": len(groups),
                "offset": offset,
                "limit": limit
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "user_id": str(current_user.get("id")),
                "offset": offset,
                "limit": limit
            },
            log_message="Failed to list groups"
        )

@router.get("/{group_id}")
async def get_group(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get detailed group information."""
    try:
        # Validate access
        if not await reference_manager.validate_access(
            user_id=str(current_user.get("id")),
            resource_type="Group",
            resource_id=group_id
        ):
            raise AuthorizationError(
                "Not authorized to access group",
                context={
                    "user_id": str(current_user.get("id")),
                    "group_id": group_id
                }
            )

        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )
            
        # Get enriched group data
        group_data = group.to_dict()
        
        # Add performance metrics if available
        try:
            metrics = await performance_service.get_group_metrics(
                group_id=group_id,
                date=datetime.utcnow()
            )
            group_data["performance"] = metrics
        except Exception as e:
            logger.warning(
                f"Failed to get group metrics: {str(e)}",
                extra={
                    "group_id": group_id,
                    "error": str(e)
                }
            )

        # Add WebSocket status
        try:
            ws_status = await ws_manager.get_group_status(group_id)
            group_data["websocket_status"] = ws_status
        except Exception as e:
            logger.warning(
                f"Failed to get WebSocket status: {str(e)}",
                extra={
                    "group_id": group_id,
                    "error": str(e)
                }
            )

        return ServiceResponse(
            success=True,
            message="Group retrieved successfully",
            data={"group": group_data}
        )

    except (NotFoundError, AuthorizationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "user_id": str(current_user.get("id")),
                "group_id": group_id
            },
            log_message="Failed to get group"
        )

@router.post("/")
async def create_group(
    data: CreateGroupRequest,
    request: Request,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Create new account group."""
    try:
        # Validate accounts exist
        for account_id in data.accounts:
            if not await reference_manager.validate_reference(
                source_type="Group",
                target_type="Account",
                reference_id=account_id
            ):
                raise ValidationError(
                    "Invalid account reference",
                    context={"account_id": account_id}
                )

        # Create group
        group = AccountGroup(
            name=data.name,
            description=data.description,
            accounts=data.accounts,
            max_drawdown=data.max_drawdown,
            target_monthly_roi=data.target_monthly_roi,
            risk_limit=data.risk_limit
        )
        await group.save()

        logger.info(
            "Created account group",
            extra={
                "group_id": str(group.id),
                "accounts": len(data.accounts)
            }
        )

        return ServiceResponse(
            success=True,
            message="Group created successfully",
            data={"group": group.to_dict()}
        )

    except ValidationError:
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "request_data": data.dict(),
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to create group"
        )

@router.patch("/{group_id}")
async def update_group(
    data: UpdateGroupRequest,
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Update group settings."""
    try:
        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        # Update basic settings
        if data.description is not None:
            group.description = data.description
        if data.max_drawdown is not None:
            group.max_drawdown = data.max_drawdown
        if data.target_monthly_roi is not None:
            group.target_monthly_roi = data.target_monthly_roi
        if data.risk_limit is not None:
            group.risk_limit = data.risk_limit

        # Update accounts if provided
        if data.accounts is not None:
            # Validate accounts exist
            for account_id in data.accounts:
                if not await reference_manager.validate_reference(
                    source_type="Group",
                    target_type="Account",
                    reference_id=account_id
                ):
                    raise ValidationError(
                        "Invalid account reference",
                        context={"account_id": account_id}
                    )
            group.accounts = data.accounts

        await group.save()

        logger.info(
            "Updated group settings",
            extra={
                "group_id": group_id,
                "updated_fields": data.dict(exclude_unset=True)
            }
        )

        return ServiceResponse(
            success=True,
            message="Group updated successfully", 
            data={"group": group.to_dict()}
        )

    except (NotFoundError, ValidationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "request_data": data.dict(),
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to update group"
        )

@router.get("/{group_id}/performance")
async def get_group_performance(
    request: Request,
    group_id: str = Path(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get group performance metrics."""
    try:
        # Validate access
        if not await reference_manager.validate_access(
            user_id=str(current_user.get("id")),
            resource_type="Group",
            resource_id=group_id
        ):
            raise AuthorizationError(
                "Not authorized to access group",
                context={
                    "user_id": str(current_user.get("id")),
                    "group_id": group_id
                }
            )

        # Get performance metrics
        metrics = await performance_service.get_group_performance(
            group_id=group_id,
            start_date=start_date,
            end_date=end_date
        )

        return ServiceResponse(
            success=True,
            message="Performance data retrieved",
            data={"performance": metrics}
        )

    except (AuthorizationError, ValidationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "date_range": f"{start_date} to {end_date}",
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to get group performance"
        )

@router.get("/{group_id}/metrics")
async def get_group_metrics(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get current group metrics and health status."""
    try:
        # Validate access
        if not await reference_manager.validate_access(
            user_id=str(current_user.get("id")),
            resource_type="Group",
            resource_id=group_id
        ):
            raise AuthorizationError(
                "Not authorized to access group",
                context={
                    "user_id": str(current_user.get("id")),
                    "group_id": group_id
                }
            )

        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        # Get current metrics
        metrics = await group.get_risk_metrics()

        # Get WebSocket health
        ws_health = await group.verify_websocket_health()

        # Get balance status
        balance_status = await group.sync_balances()

        return ServiceResponse(
            success=True,
            message="Group metrics retrieved",
            data={
                "risk_metrics": metrics,
                "websocket_health": ws_health,
                "balance_status": balance_status
            }
        )

    except (NotFoundError, AuthorizationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to get group metrics"
        )

@router.get("/{group_id}/history")
async def get_group_history(
    request: Request,
    group_id: str = Path(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    interval: str = Query("day", regex="^(day|week|month)$"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """Get historical group performance data."""
    try:
        # Validate access
        if not await reference_manager.validate_access(
            user_id=str(current_user.get("id")),
            resource_type="Group",
            resource_id=group_id
        ):
            raise AuthorizationError(
                "Not authorized to access group",
                context={
                    "user_id": str(current_user.get("id")),
                    "group_id": group_id
                }
            )

        # Get performance history
        history = await performance_service.get_historical_metrics(
            group_id=group_id,
            start_date=start_date,
            end_date=end_date,
            interval=interval
        )

        return ServiceResponse(
            success=True,
            message="Historical data retrieved",
            data={
                "history": history,
                "interval": interval,
                "period": {
                    "start": start_date,
                    "end": end_date
                }
            }
        )

    except (AuthorizationError, ValidationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "date_range": f"{start_date} to {end_date}",
                "interval": interval,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to get group history"
        )

@router.post("/{group_id}/accounts")
async def bulk_add_accounts(
    data: BulkAccountsRequest,
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Add multiple accounts to group."""
    try:
        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        results = {
            "success": [],
            "failed": []
        }

        for account_id in data.account_ids:
            try:
                await group.add_account(account_id)
                results["success"].append(account_id)
            except Exception as e:
                results["failed"].append({
                    "account_id": account_id,
                    "error": str(e)
                })

        logger.info(
            "Bulk added accounts to group",
            extra={
                "group_id": group_id,
                "success_count": len(results["success"]),
                "failed_count": len(results["failed"])
            }
        )

        return ServiceResponse(
            success=True,
            message="Bulk account addition completed",
            data={
                "results": results,
                "group": group.to_dict()
            }
        )

    except NotFoundError:
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "account_ids": data.account_ids,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to bulk add accounts"
        )

@router.post("/{group_id}/accounts/{account_id}")
async def add_account(
    request: Request,
    group_id: str = Path(...),
    account_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Add account to group."""
    try:
        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        # Validate account exists
        if not await reference_manager.validate_reference(
            source_type="Group",
            target_type="Account",
            reference_id=account_id
        ):
            raise ValidationError(
                "Invalid account reference",
                context={"account_id": account_id}
            )

        await group.add_account(account_id)

        logger.info(
            "Added account to group",
            extra={
                "group_id": group_id,
                "account_id": account_id
            }
        )

        return ServiceResponse(
            success=True,
            message="Account added to group",
            data={"group": group.to_dict()}
        )

    except (NotFoundError, ValidationError):
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "account_id": account_id,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to add account to group"
        )

@router.delete("/{group_id}/accounts/{account_id}")
async def remove_account(
    request: Request,
    group_id: str = Path(...),
    account_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Remove account from group."""
    try:
        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        await group.remove_account(account_id)

        logger.info(
            "Removed account from group",
            extra={
                "group_id": group_id,
                "account_id": account_id
            }
        )

        return ServiceResponse(
            success=True,
            message="Account removed from group",
            data={"group": group.to_dict()}
        )

    except NotFoundError:
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "account_id": account_id,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to remove account from group"
        )

@router.delete("/{group_id}")
async def delete_group(
    request: Request,
    group_id: str = Path(...),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """Delete an account group."""
    try:
        group = await AccountGroup.get(group_id)
        if not group:
            raise NotFoundError(
                "Group not found",
                context={"group_id": group_id}
            )

        # Remove all references first
        for account_id in group.accounts:
            await group.remove_account(account_id)

        # Delete group
        await group.delete()

        logger.info(
            "Deleted group",
            extra={
                "group_id": group_id,
                "account_count": len(group.accounts)
            }
        )

        return ServiceResponse(
            success=True,
            message="Group deleted successfully",
            data={"group_id": group_id}
        )

    except NotFoundError:
        raise
    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "group_id": group_id,
                "user_id": str(current_user.get("id"))
            },
            log_message="Failed to delete group"
        )

# Import models and dependencies at end to avoid circular imports
from app.models.group import AccountGroup
from app.api.v1.deps import (
    get_current_user,
    get_admin_user,
    get_service_deps,
    get_accessible_accounts
)

# Import services at end to avoid circular dependencies
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager