"""
User management endpoints with consistent error handling and standardized responses.

This module provides HTTP endpoints for user management operations with a
group-based access control model.
"""

from fastapi import APIRouter, Depends, Request, Query, Path, status
from beanie import PydanticObjectId
from typing import Dict, List, Optional, Any

from app.crud.crud_user import user, UserCreate, UserUpdate
from app.core.references import UserRole
from app.core.logging.logger import get_logger
from app.api.v1.deps import get_admin_user, get_current_user
from app.api.v1.references import ServiceResponse

router = APIRouter()
logger = get_logger(__name__)


@router.get("/")
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    role: Optional[str] = None,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    List users with optional filtering by role.
    Admin only.
    """
    # Build query with optional role filter
    query = {}
    if role:
        try:
            # Convert string role to enum
            query["role"] = UserRole[role.upper()]
        except (KeyError, ValueError):
            # If invalid role, use empty query (return all users)
            logger.warning(f"Invalid role filter: {role}")
            
    # Get users and total count
    users_list = await user.get_multi(skip=skip, limit=limit, query=query)
    total = await user.model.find(query).count()
    
    logger.info(
        "Listed users", 
        extra={
            "count": len(users_list), 
            "role_filter": role,
            "total": total,
            "user_id": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Users retrieved successfully",
        data={
            "users": [u.get_user_info() for u in users_list],
            "pagination": {
                "total": total,
                "skip": skip,
                "limit": limit
            }
        }
    )


@router.get("/{user_id}")
async def get_user(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Get user details by ID.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Get user by ID, letting global error handlers manage exceptions
    user_obj = await user.get(obj_id)
    
    logger.info(
        "Retrieved user", 
        extra={
            "user_id": user_id,
            "requester_id": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="User retrieved successfully",
        data={"user": user_obj.get_user_info()}
    )


@router.post("/")
async def create_user(
    request: Request,
    user_data: UserCreate,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new user.
    Admin only.
    """
    # Create user via CRUD service
    new_user = await user.create(user_data)
    
    logger.info(
        "Created new user",
        extra={
            "username": user_data.username,
            "role": user_data.role.value,
            "user_id": str(new_user.id),
            "created_by": str(current_user.get("id"))
        }
    )
    
    # Update created_by field
    new_user.created_by = str(current_user.get("id"))
    await new_user.save()
    
    return ServiceResponse(
        success=True,
        message="User created successfully",
        data={"user": new_user.get_user_info()}
    )


@router.patch("/{user_id}")
async def update_user(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    user_data: UserUpdate = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update an existing user.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Update user via CRUD service
    updated_user = await user.update(obj_id, user_data)
    
    # Update modified_by field
    updated_user.modified_by = str(current_user.get("id"))
    await updated_user.save()
    
    logger.info(
        "Updated user",
        extra={
            "user_id": user_id,
            "fields": list(user_data.model_dump(exclude_unset=True).keys()),
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="User updated successfully",
        data={"user": updated_user.get_user_info()}
    )


@router.post("/{user_id}/assign-groups")
async def assign_user_groups(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    group_ids: List[str] = ...,
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Assign groups to a user.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Assign groups via CRUD service
    updated_user = await user.assign_groups(obj_id, group_ids)
    
    # Update modified_by field
    updated_user.modified_by = str(current_user.get("id"))
    await updated_user.save()
    
    logger.info(
        "Assigned groups to user",
        extra={
            "user_id": user_id,
            "group_count": len(group_ids),
            "modified_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Groups assigned successfully",
        data={
            "user": updated_user.get_user_info(),
            "assigned_groups": group_ids
        }
    )


@router.delete("/{user_id}")
async def delete_user(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Delete a user.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Get user for logging purposes
    user_obj = await user.get(obj_id)
    username = user_obj.username
    
    # Delete user via CRUD service
    await user.delete(obj_id)
    
    logger.info(
        "Deleted user", 
        extra={
            "user_id": user_id,
            "username": username,
            "deleted_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="User deleted successfully",
        data={"user_id": user_id}
    )


@router.get("/me/groups")
async def get_my_groups(
    request: Request,
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get groups assigned to the current user.
    """
    # Get groups via CRUD service
    groups = await user.get_assigned_groups(str(current_user.get("id")))
    
    return ServiceResponse(
        success=True,
        message="Groups retrieved successfully",
        data={"groups": groups}
    )


@router.get("/me/accessible-accounts")
async def get_my_accessible_accounts(
    request: Request,
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Get accounts accessible to the current user through group membership.
    """
    # Get accessible accounts via CRUD service
    accounts = await user.get_accessible_accounts(str(current_user.get("id")))
    
    return ServiceResponse(
        success=True,
        message="Accessible accounts retrieved successfully",
        data={"accounts": accounts}
    )


@router.post("/{user_id}/activate")
async def activate_user(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Activate a deactivated user.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Update user via CRUD service
    updated_user = await user.update(obj_id, {"is_active": True})
    
    logger.info(
        "Activated user",
        extra={
            "user_id": user_id,
            "activated_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="User activated successfully",
        data={"user": updated_user.get_user_info()}
    )


@router.post("/{user_id}/deactivate")
async def deactivate_user(
    request: Request,
    user_id: str = Path(..., description="User ID"),
    current_user: Dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Deactivate an active user.
    Admin only.
    """
    # Convert string ID to ObjectId
    obj_id = PydanticObjectId(user_id)
    
    # Update user via CRUD service
    updated_user = await user.update(obj_id, {"is_active": False})
    
    logger.info(
        "Deactivated user",
        extra={
            "user_id": user_id,
            "deactivated_by": str(current_user.get("id"))
        }
    )
    
    return ServiceResponse(
        success=True,
        message="User deactivated successfully",
        data={"user": updated_user.get_user_info()}
    )


@router.get("/check-account-access/{account_id}")
async def check_account_access(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    current_user: Dict = Depends(get_current_user)
) -> ServiceResponse:
    """
    Check if the current user has access to a specific account.
    """
    # Check account access via CRUD service
    has_access = await user.check_account_access(
        user_id=str(current_user.get("id")),
        account_id=account_id
    )
    
    return ServiceResponse(
        success=True,
        message="Access check completed",
        data={
            "account_id": account_id,
            "has_access": has_access
        }
    )