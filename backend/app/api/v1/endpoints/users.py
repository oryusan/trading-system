"""
User management endpoint.
Only administrator users are allowed to perform these operations.
"""

from fastapi import APIRouter, Depends, Request, Query, Path
from beanie import PydanticObjectId
from typing import List, Optional

from app.crud.crud_user import user, UserCreate, UserUpdate
from app.core.errors.base import ValidationError, NotFoundError, DatabaseError
from app.core.logging.logger import get_logger
from app.api.v1.deps import get_admin_user
from app.api.v1.references import ServiceResponse

router = APIRouter()
logger = get_logger(__name__)


@router.get("/")
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    role: Optional[str] = None,
    current_user: dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    List users with optional filtering by role.
    Admin only.
    """
    query = {}
    if role:
        query["role"] = role
        
    users = await user.get_multi(skip=skip, limit=limit, query=query)
    total = await user.model.find(query).count()
    
    logger.info("Listed users", extra={"count": len(users), "role_filter": role})
    return ServiceResponse(
        success=True,
        message="Users retrieved successfully",
        data={
            "users": [u.get_user_info() for u in users],
            "pagination": {
                "total": total,
                "skip": skip,
                "limit": limit
            }
        }
    )


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Get user details by ID.
    Admin only.
    """
    try:
        user_obj = await user.get(PydanticObjectId(user_id))
        logger.info("Retrieved user", extra={"user_id": user_id})
        return ServiceResponse(
            success=True,
            message="User retrieved successfully",
            data={"user": user_obj.get_user_info()}
        )
    except NotFoundError as e:
        logger.warning(f"User not found: {user_id}")
        raise e
    except ValidationError as e:
        logger.warning(f"Invalid user ID format: {user_id}")
        raise e


@router.post("/")
async def create_user(
    request: Request,
    user_in: UserCreate,
    current_user: dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Create a new user.
    Admin only.
    """
    try:
        new_user = await user.create(user_in)
        logger.info(
            "Created new user",
            extra={
                "username": user_in.username,
                "role": user_in.role,
                "user_id": str(new_user.id)
            }
        )
        return ServiceResponse(
            success=True,
            message="User created successfully",
            data={"user": new_user.get_user_info()}
        )
    except ValidationError as e:
        logger.warning(f"User creation failed: {str(e)}")
        raise e


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    request: Request,
    user_in: UserUpdate,
    current_user: dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Update an existing user.
    Admin only.
    """
    try:
        updated_user = await user.update(PydanticObjectId(user_id), user_in)
        logger.info(
            "Updated user",
            extra={
                "user_id": str(user_id),
                "fields": list(user_in.model_dump(exclude_unset=True).keys())
            }
        )
        return ServiceResponse(
            success=True,
            message="User updated successfully",
            data={"user": updated_user.get_user_info()}
        )
    except NotFoundError as e:
        logger.warning(f"User not found for update: {user_id}")
        raise e
    except ValidationError as e:
        logger.warning(f"Invalid update data: {str(e)}")
        raise e


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_admin_user)
) -> ServiceResponse:
    """
    Delete a user.
    Admin only.
    """
    try:
        await user.delete(PydanticObjectId(user_id))
        logger.info("Deleted user", extra={"user_id": user_id})
        return ServiceResponse(
            success=True,
            message="User deleted successfully",
            data={"user_id": user_id}
        )
    except NotFoundError as e:
        logger.warning(f"User not found for deletion: {user_id}")
        raise e
    except ValidationError as e:
        logger.warning(f"Cannot delete user: {str(e)}")
        raise e