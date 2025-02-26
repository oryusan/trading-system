"""
User CRUD operations with proper error handling.

Features:
- Input validation with rich context via Pydantic.
- Custom error types (ValidationError, NotFoundError, etc.).
- Reference validation for assignments.
- Authentication tracking.
"""

from typing import List, Optional, Dict, Any, Union

from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator, model_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.user import User
from app.core.enums import UserRole
from app.core.errors.base import (
    DatabaseError,
    ValidationError,
    NotFoundError,
    AuthorizationError
)
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

logger = get_logger(__name__)

class UserCreate(BaseModel):
    """Schema for creating a new user."""
    username: str
    password: str
    role: UserRole
    assigned_accounts: List[str] = []
    assigned_groups: List[str] = []
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not v.strip():
            raise ValidationError(
                "Username cannot be empty",
                context={"username": v}
            )
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValidationError(
                "Password must be at least 8 characters",
                context={"password_length": len(v)}
            )
        return v

    @model_validator(mode="after")
    def check_assignments(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        role = values.get("role")
        if role == UserRole.VIEWER and values.get("assigned_groups"):
            raise ValidationError(
                "Viewer users cannot have assigned groups",
                context={"role": role}
            )
        if role == UserRole.EXPORTER and values.get("assigned_accounts"):
            raise ValidationError(
                "Exporter users cannot have assigned accounts",
                context={"role": role}
            )
        return values

class UserUpdate(BaseModel):
    """Schema for updating a user."""
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[UserRole] = None
    assigned_accounts: Optional[List[str]] = None
    assigned_groups: Optional[List[str]] = None
    is_active: Optional[bool] = None

class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    """CRUD operations for the User model with enhanced error handling."""

    @handle_db_error("Failed to get user by username", lambda self, username: {"username": username})
    async def get_by_username(self, username: str) -> User:
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        return user

    @handle_db_error("Failed to validate username uniqueness", lambda self, username, exclude_id=None: {"username": username, "exclude_id": str(exclude_id) if exclude_id else None})
    async def validate_username_unique(
        self,
        username: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        query = {"username": username}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        return not await User.find_one(query)

    @handle_db_error("Failed to validate admin constraints", lambda self, role=None, current_role=None: {"role": role, "current_role": current_role})
    async def validate_admin_constraints(
        self,
        role: Optional[UserRole] = None,
        current_role: Optional[UserRole] = None
    ) -> None:
        if role == UserRole.ADMIN:
            existing_admin = await User.find_one({"role": UserRole.ADMIN})
            if existing_admin:
                raise ValidationError(
                    "An admin user already exists",
                    context={"existing_admin": existing_admin.username}
                )
        elif current_role == UserRole.ADMIN and role != UserRole.ADMIN:
            admin_count = await User.find({"role": UserRole.ADMIN}).count()
            if admin_count <= 1:
                raise ValidationError(
                    "Cannot remove the last admin user",
                    context={"admin_count": admin_count}
                )

    @handle_db_error("Failed to create user", lambda self, obj_in: {"username": obj_in.username, "role": obj_in.role})
    async def create(self, obj_in: UserCreate) -> User:
        if not await self.validate_username_unique(obj_in.username):
            raise ValidationError(
                "Username already exists",
                context={"username": obj_in.username}
            )
        await self.validate_admin_constraints(role=obj_in.role)
        db_obj = User(
            username=obj_in.username,
            hashed_password=get_password(obj_in.password),
            role=obj_in.role,
            assigned_accounts=obj_in.assigned_accounts,
            assigned_groups=obj_in.assigned_groups,
            is_active=obj_in.is_active
        )
        await db_obj.insert()
        logger.info(
            "Created new user",
            extra={
                "username": obj_in.username,
                "role": obj_in.role,
                "user_id": str(db_obj.id)
            }
        )
        return db_obj

    @handle_db_error("Failed to update user", lambda self, id, obj_in: {"user_id": str(id), "fields": list(obj_in.model_dump(exclude_unset=True).keys()) if not isinstance(obj_in, dict) else list(obj_in.keys())})
    async def update(
        self,
        id: PydanticObjectId,
        obj_in: Union[UserUpdate, Dict[str, Any]]
    ) -> User:
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        user = await self.get(id)
        if "username" in update_data and update_data["username"] != user.username:
            if not await self.validate_username_unique(update_data["username"], id):
                raise ValidationError(
                    "Username already exists",
                    context={"username": update_data["username"]}
                )
        if "password" in update_data:
            update_data["hashed_password"] = get_password(update_data["password"])
            del update_data["password"]
        if "role" in update_data:
            new_role = update_data["role"]
            if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
                await self.validate_admin_constraints(role=new_role, current_role=user.role)
            if new_role != user.role:
                if new_role == UserRole.VIEWER:
                    update_data["assigned_groups"] = []
                elif new_role == UserRole.EXPORTER:
                    update_data["assigned_accounts"] = []
        for field, value in update_data.items():
            setattr(user, field, value)
        await user.save()
        logger.info(
            "Updated user",
            extra={
                "user_id": str(id),
                "username": user.username,
                "fields": list(update_data.keys())
            }
        )
        return user

    @handle_db_error("Failed to authenticate user", lambda self, username, password: {"username": username})
    async def authenticate(self, username: str, password: str) -> User:
        user = await self.get_by_username(username)
        if not verify_password(password, user.hashed_password):
            raise AuthorizationError(
                "Invalid credentials",
                context={"username": username}
            )
        return user

    @handle_db_error("Failed to get users by role", lambda self, role, skip, limit: {"role": role, "skip": skip, "limit": limit})
    async def get_users_by_role(
        self,
        role: UserRole,
        skip: int = 0,
        limit: int = 100
    ) -> List[User]:
        return await User.find({"role": role}).skip(skip).limit(limit).to_list()

    @handle_db_error("Failed to assign accounts to user", lambda self, user_id, account_ids: {"user_id": str(user_id), "account_ids": account_ids})
    async def assign_accounts(
        self,
        user_id: PydanticObjectId,
        account_ids: List[str]
    ) -> User:
        user = await self.get(user_id)
        if user.role != UserRole.VIEWER:
            raise AuthorizationError(
                "Only viewer users can be assigned accounts",
                context={"user_id": str(user_id), "role": user.role}
            )
        for account_id in account_ids:
            if not await reference_manager.validate_reference(
                source_type="User",
                target_type="Account",
                reference_id=account_id
            ):
                raise ValidationError(
                    "Invalid account reference",
                    context={"user_id": str(user_id), "account_id": account_id}
                )
        user.assigned_accounts = account_ids
        await user.save()
        logger.info(
            "Assigned accounts to user",
            extra={"user_id": str(user_id), "account_count": len(account_ids)}
        )
        return user

    @handle_db_error("Failed to assign groups to user", lambda self, user_id, group_ids: {"user_id": str(user_id), "group_ids": group_ids})
    async def assign_groups(
        self,
        user_id: PydanticObjectId,
        group_ids: List[str]
    ) -> User:
        user = await self.get(user_id)
        if user.role != UserRole.EXPORTER:
            raise AuthorizationError(
                "Only exporter users can be assigned groups",
                context={"user_id": str(user_id), "role": user.role}
            )
        for group_id in group_ids:
            if not await reference_manager.validate_reference(
                source_type="User",
                target_type="Group",
                reference_id=group_id
            ):
                raise ValidationError(
                    "Invalid group reference",
                    context={"user_id": str(user_id), "group_id": group_id}
                )
        user.assigned_groups = group_ids
        await user.save()
        logger.info(
            "Assigned groups to user",
            extra={"user_id": str(user_id), "group_count": len(group_ids)}
        )
        return user

    @handle_db_error("Failed to delete user", lambda self, id: {"user_id": str(id)})
    async def delete(self, id: PydanticObjectId) -> bool:
        user = await self.get(id)
        if user.role == UserRole.ADMIN:
            admin_count = await User.find({"role": UserRole.ADMIN}).count()
            if admin_count <= 1:
                raise ValidationError(
                    "Cannot delete the last admin user",
                    context={"user_id": str(id)}
                )
        await user.delete()
        logger.info(
            "Deleted user",
            extra={"user_id": str(id), "username": user.username}
        )
        return True

# Import at end to avoid circular dependencies
from app.services.reference.manager import reference_manager
from app.services.auth.password import password_manager

# Create a singleton instance of the CRUDUser service.
user = CRUDUser(User)
