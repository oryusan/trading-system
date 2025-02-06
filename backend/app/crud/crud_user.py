"""
Enhanced user CRUD operations with proper error handling.

Features:
- Input validation with rich context
- Proper error type usage
- Reference validation
- Authentication tracking
- No API error handling
"""

from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator

from app.crud.base import CRUDBase
from app.models.user import User
from app.core.references import UserRole
from app.core.errors import (
    DatabaseError,
    ValidationError,
    NotFoundError, 
    AuthorizationError
)
from app.core.logging.logger import get_logger

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
        """Validate username is not empty."""
        if not v.strip():
            raise ValidationError(
                "Username cannot be empty",
                context={"username": v}
            )
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password meets requirements."""
        if len(v) < 8:
            raise ValidationError(
                "Password must be at least 8 characters",
                context={"password_length": len(v)}
            )
        return v

    @field_validator("assigned_accounts", "assigned_groups")
    @classmethod 
    def validate_assignments(cls, v: List[str], info: Any) -> List[str]:
        """Validate role-specific assignments."""
        role = info.data.get("role")
        if role == UserRole.VIEWER and "assigned_groups" in info.data and info.data["assigned_groups"]:
            raise ValidationError(
                "Viewer users cannot have assigned groups",
                context={"role": role}
            )
        if role == UserRole.EXPORTER and "assigned_accounts" in info.data and info.data["assigned_accounts"]:
            raise ValidationError(
                "Exporter users cannot have assigned accounts", 
                context={"role": role}
            )
        return v

class UserUpdate(BaseModel):
    """Schema for updating a user."""
    username: Optional[str] = None
    password: Optional[str] = None  
    role: Optional[UserRole] = None
    assigned_accounts: Optional[List[str]] = None
    assigned_groups: Optional[List[str]] = None
    is_active: Optional[bool] = None

class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    """CRUD operations for User model with enhanced error handling."""

    async def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        try:
            user = await User.find_one({"username": username})
            if not user:
                raise NotFoundError(
                    "User not found",
                    context={"username": username}
                )
            return user
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve user by username",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

    async def validate_username_unique(
        self,
        username: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        """Check if username is unique."""
        try:
            query = {"username": username}
            if exclude_id:
                query["_id"] = {"$ne": exclude_id}
            return not await self.exists(query)
        except Exception as e:
            raise DatabaseError(
                "Failed to validate username uniqueness",
                context={
                    "username": username, 
                    "exclude_id": str(exclude_id) if exclude_id else None,
                    "error": str(e)
                }
            )

    async def validate_admin_constraints(
        self,
        role: Optional[UserRole] = None,
        current_role: Optional[UserRole] = None
    ) -> None:
        """
        Validate admin role constraints:
        - Only one admin can exist
        - Can't remove last admin user
        """
        try:
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
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to validate admin constraints",
                context={
                    "role": role,
                    "current_role": current_role,
                    "error": str(e)
                }
            )

    async def create(self, obj_in: UserCreate) -> User:
        """Create new user with validation."""
        try:
            if not await self.validate_username_unique(obj_in.username):
                raise ValidationError(
                    "Username already exists",
                    context={"username": obj_in.username}
                )

            await self.validate_admin_constraints(role=obj_in.role)

            db_obj = User(
                username=obj_in.username,
                hashed_password=get_password_hash(obj_in.password),
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

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to create user",
                context={
                    "username": obj_in.username,
                    "role": obj_in.role,
                    "error": str(e)
                }
            )

    async def update(
        self,
        id: PydanticObjectId,
        obj_in: Union[UserUpdate, Dict[str, Any]]
    ) -> Optional[User]:
        """Update user with validation."""
        try:
            update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
            user = await self.get(id)  # This will raise NotFoundError if needed

            # Check username uniqueness
            if "username" in update_data and update_data["username"] != user.username:
                if not await self.validate_username_unique(update_data["username"], id):
                    raise ValidationError(
                        "Username already exists",
                        context={"username": update_data["username"]}
                    )

            # Handle password update
            if "password" in update_data:
                update_data["hashed_password"] = get_password_hash(update_data["password"])
                del update_data["password"]

            # Role change validation
            if "role" in update_data:
                new_role = update_data["role"]
                if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
                    await self.validate_admin_constraints(
                        role=new_role,
                        current_role=user.role
                    )

                # Clear incompatible assignments
                if new_role != user.role:
                    if new_role == UserRole.VIEWER:
                        update_data["assigned_groups"] = []
                    elif new_role == UserRole.EXPORTER:
                        update_data["assigned_accounts"] = []

            # Update fields
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

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update user",
                context={
                    "user_id": str(id),
                    "error": str(e)
                }
            )

    async def authenticate(self, username: str, password: str) -> Optional[User]:
        """Authenticate user by username and password."""
        try:
            user = await self.get_by_username(username)
            if not verify_password(password, user.hashed_password):
                raise AuthorizationError(
                    "Invalid credentials",
                    context={"username": username}
                )
            return user
        except NotFoundError:
            raise AuthorizationError(
                "Invalid credentials",
                context={"username": username}
            )
        except Exception as e:
            raise DatabaseError(
                "Authentication failed",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

    async def get_users_by_role(
        self,
        role: UserRole,
        skip: int = 0,
        limit: int = 100
    ) -> List[User]:
        """Get paginated list of users by role."""
        try:
            return await User.find({"role": role}).skip(skip).limit(limit).to_list()
        except Exception as e:
            raise DatabaseError(
                "Failed to get users by role",
                context={
                    "role": role,
                    "skip": skip,
                    "limit": limit,
                    "error": str(e)
                }
            )

    async def assign_accounts(
        self,
        user_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Optional[User]:
        """Assign accounts to viewer user."""
        try:
            user = await self.get(user_id)
            if user.role != UserRole.VIEWER:
                raise AuthorizationError(
                    "Only viewer users can be assigned accounts",
                    context={
                        "user_id": str(user_id),
                        "role": user.role
                    }
                )

            # Validate accounts before assignment
            for account_id in account_ids:
                if not await reference_manager.validate_reference(
                    source_type="User",
                    target_type="Account",
                    reference_id=account_id
                ):
                    raise ValidationError(
                        "Invalid account reference",
                        context={
                            "user_id": str(user_id),
                            "account_id": account_id
                        }
                    )

            user.assigned_accounts = account_ids
            await user.save()

            logger.info(
                "Assigned accounts to user",
                extra={
                    "user_id": str(user_id),
                    "account_count": len(account_ids)
                }
            )

            return user

        except (NotFoundError, AuthorizationError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to assign accounts",
                context={
                    "user_id": str(user_id),
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def assign_groups(
        self,
        user_id: PydanticObjectId,
        group_ids: List[str]
    ) -> Optional[User]:
        """Assign groups to exporter user."""
        try:
            user = await self.get(user_id)
            if user.role != UserRole.EXPORTER:
                raise AuthorizationError(
                    "Only exporter users can be assigned groups",
                    context={
                        "user_id": str(user_id),
                        "role": user.role
                    }
                )

            # Validate groups before assignment
            for group_id in group_ids:
                if not await reference_manager.validate_reference(
                    source_type="User",
                    target_type="Group",
                    reference_id=group_id
                ):
                    raise ValidationError(
                        "Invalid group reference",
                        context={
                            "user_id": str(user_id),
                            "group_id": group_id
                        }
                    )

            user.assigned_groups = group_ids
            await user.save()

            logger.info(
                "Assigned groups to user",
                extra={
                    "user_id": str(user_id),
                    "group_count": len(group_ids)
                }
            )

            return user

        except (NotFoundError, AuthorizationError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to assign groups",
                context={
                    "user_id": str(user_id),
                    "group_ids": group_ids,
                    "error": str(e)
                }
            )

    async def delete(self, id: PydanticObjectId) -> bool:
        """Delete user with admin role protection."""
        try:
            user = await self.get(id)

            # Protect last admin
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
                extra={
                    "user_id": str(id),
                    "username": user.username
                }
            )

            return True

        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to delete user",
                context={
                    "user_id": str(id),
                    "error": str(e)
                }
            )

# Import at end to avoid circular imports            
from app.services.reference.manager import reference_manager
from app.core.security import get_password_hash, verify_password

# Create singleton instance
user = CRUDUser(User)