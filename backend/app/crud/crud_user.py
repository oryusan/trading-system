"""
User CRUD operations with centralized service integration and group-based access model.

This module centralizes all user-related operations, including:
- User creation, retrieval, updates and deletion
- Password management and authentication  
- Group-based access control management
"""

from typing import List, Optional, Dict, Any, Union, Set
import asyncio
from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator, model_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.user import User
from app.core.references import UserRole
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
    """Schema for creating a new user with validation."""
    username: str
    password: str
    role: UserRole
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


class UserUpdate(BaseModel):
    """Schema for updating a user with validation."""
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[UserRole] = None
    assigned_groups: Optional[List[str]] = None
    is_active: Optional[bool] = None


class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    """
    CRUD operations for the User model with centralized service integration.
    
    This class handles all user-related operations and integrates with external
    services like password management and reference validation.
    """

    @handle_db_error("Failed to get user by username", lambda self, username: {"username": username})
    async def get_by_username(self, username: str) -> User:
        """Get a user by username."""
        user = await User.find_one({"username": username.lower()})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        return user

    @handle_db_error("Failed to validate username uniqueness", lambda self, username, exclude_id=None: {"username": username, "exclude_id": str(exclude_id) if exclude_id else None})
    async def validate_username_unique(
        self,
        username: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        """Check if a username is available (unique)."""
        query = {"username": username.lower()}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        existing_user = await User.find_one(query)
        return existing_user is None

    @handle_db_error("Failed to validate admin constraints", lambda self, role=None, current_role=None: {"role": role.value if role else None, "current_role": current_role.value if current_role else None})
    async def validate_admin_constraints(
        self,
        role: Optional[UserRole] = None,
        current_role: Optional[UserRole] = None
    ) -> None:
        """
        Validate constraints for admin users.
        
        - Only one admin allowed in the system
        - The last admin cannot have role changed or be deleted
        """
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

    async def validate_group_references(self, group_ids: List[str]) -> None:
        """
        Validate that group references exist.
        
        Args:
            group_ids: List of group IDs to validate
        
        Raises:
            ValidationError: If there are duplicates
            NotFoundError: If a reference doesn't exist
        """
        # Check for duplicates
        if len(set(group_ids)) != len(group_ids):
            duplicates = [grp for grp in set(group_ids) if group_ids.count(grp) > 1]
            raise ValidationError(
                "Duplicate group assignments",
                context={"duplicates": duplicates}
            )
        
        # Validate all group references concurrently
        tasks = [self._validate_reference("Group", group_id) for group_id in group_ids]
                
        if tasks:
            await asyncio.gather(*tasks)
            
    async def _validate_reference(self, entity_type: str, reference_id: str) -> None:
        """
        Validate a single reference to ensure it exists.
        
        Args:
            entity_type: Type of entity ("Account" or "Group")
            reference_id: ID of the entity to check
            
        Raises:
            NotFoundError: If the reference doesn't exist
        """
        valid = await reference_manager.validate_reference(
            source_type="User",
            target_type=entity_type,
            reference_id=reference_id
        )
        
        if not valid:
            raise NotFoundError(
                f"Referenced {entity_type.lower()} not found",
                context={f"{entity_type.lower()}_id": reference_id}
            )

    @handle_db_error("Failed to create user", lambda self, obj_in: {"username": obj_in.username, "role": obj_in.role.value})
    async def create(self, obj_in: UserCreate) -> User:
        """
        Create a new user with proper validation and password hashing.
        """
        # Validate username uniqueness
        if not await self.validate_username_unique(obj_in.username):
            raise ValidationError(
                "Username already exists",
                context={"username": obj_in.username}
            )
            
        # Validate admin constraints
        await self.validate_admin_constraints(role=obj_in.role)
        
        # Validate group references
        if obj_in.assigned_groups:
            await self.validate_group_references(obj_in.assigned_groups)
        
        # Hash the password
        hashed_password = await password_manager.hash_password(obj_in.password)
        
        # Create the user object
        db_obj = User(
            username=obj_in.username.lower(),
            hashed_password=hashed_password,
            role=obj_in.role,
            assigned_groups=obj_in.assigned_groups,
            is_active=obj_in.is_active,
            created_at=datetime.utcnow(),
            modified_at=datetime.utcnow()
        )
        
        # Save to database
        await db_obj.insert()
        
        # Log the action
        logger.info(
            "Created new user",
            extra={
                "username": obj_in.username,
                "role": obj_in.role.value,
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
        """
        Update a user with proper validation and reference checking.
        """
        # Get the current user
        user = await self.get(id)
        
        # Convert input to dictionary if needed
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        
        # Validate username uniqueness if changing username
        if "username" in update_data and update_data["username"] != user.username:
            if not await self.validate_username_unique(update_data["username"], id):
                raise ValidationError(
                    "Username already exists",
                    context={"username": update_data["username"]}
                )
        
        # Handle password hashing if updating password
        if "password" in update_data:
            update_data["hashed_password"] = await password_manager.hash_password(update_data["password"])
            update_data["password_changed_at"] = datetime.utcnow()
            del update_data["password"]
        
        # Validate role changes for admin users
        if "role" in update_data:
            new_role = update_data["role"]
            if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
                await self.validate_admin_constraints(role=new_role, current_role=user.role)
        
        # Validate group references if updating assignments
        if "assigned_groups" in update_data and update_data["assigned_groups"] is not None:
            await self.validate_group_references(update_data["assigned_groups"])
        
        # Update all fields
        for field, value in update_data.items():
            setattr(user, field, value)
        
        # Set modified timestamp
        user.modified_at = datetime.utcnow()
        
        # Save the updated user
        await user.save()
        
        # Log the action
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
        """
        Authenticate a user with username and password.
        
        Args:
            username: The username to authenticate
            password: The plain text password to verify
            
        Returns:
            The authenticated User object
            
        Raises:
            AuthorizationError: If credentials are invalid
            NotFoundError: If user doesn't exist
        """
        # Get the user by username
        user = await self.get_by_username(username)
        
        # Verify the password
        is_valid = await password_manager.verify_password(password, user.hashed_password)
        
        if not is_valid:
            # Record failed login attempt
            await self.record_login_attempt(user.id, success=False)
            raise AuthorizationError(
                "Invalid credentials",
                context={"username": username}
            )
        
        # Check if user is locked out
        if user.is_locked_out():
            raise AuthorizationError(
                "Account temporarily locked due to too many failed attempts",
                context={"username": username, "attempts": user.login_attempts}
            )
        
        # Record successful login
        await self.record_login_attempt(user.id, success=True)
        
        return user

    @handle_db_error("Failed to record login attempt", lambda self, user_id, success: {"user_id": str(user_id), "success": success})
    async def record_login_attempt(
        self, 
        user_id: PydanticObjectId, 
        success: bool,
        ip_address: Optional[str] = None
    ) -> None:
        """
        Record a login attempt for the specified user.
        
        Args:
            user_id: ID of the user
            success: Whether the login was successful
            ip_address: Optional IP address of the client
        """
        # Get the user
        user = await self.get(user_id)
        now = datetime.utcnow()
        
        # Update user fields based on login success
        if success:
            user.last_login = now
            user.login_attempts = 0
            user.last_failed_login = None
            logger.info(
                "Successful login",
                extra={"username": user.username, "ip_address": ip_address}
            )
        else:
            user.login_attempts += 1
            user.last_failed_login = now
            logger.warning(
                "Failed login attempt",
                extra={
                    "username": user.username,
                    "attempts": user.login_attempts,
                    "ip_address": ip_address
                }
            )
        
        # Update modified timestamp
        user.modified_at = now
        
        # Save the updated user
        await user.save()

    @handle_db_error("Failed to get users by role", lambda self, role, skip, limit: {"role": role.value, "skip": skip, "limit": limit})
    async def get_users_by_role(
        self,
        role: UserRole,
        skip: int = 0,
        limit: int = 100
    ) -> List[User]:
        """Get all users with the specified role."""
        return await User.find({"role": role}).skip(skip).limit(limit).to_list()

    @handle_db_error("Failed to assign groups to user", lambda self, user_id, group_ids: {"user_id": str(user_id), "group_ids": group_ids})
    async def assign_groups(
        self,
        user_id: PydanticObjectId,
        group_ids: List[str]
    ) -> User:
        """
        Assign groups to a user.
        
        Args:
            user_id: ID of the user to assign groups to
            group_ids: List of group IDs to assign
            
        Returns:
            Updated User object
            
        Raises:
            ValidationError: If group references are invalid
        """
        # Get the user
        user = await self.get(user_id)
        
        # Validate group references
        await self.validate_group_references(group_ids)
        
        # Update assigned groups
        user.assigned_groups = group_ids
        user.modified_at = datetime.utcnow()
        
        # Save the updated user
        await user.save()
        
        # Log the action
        logger.info(
            "Assigned groups to user",
            extra={"user_id": str(user_id), "group_count": len(group_ids)}
        )
        
        return user

    @handle_db_error("Failed to get assigned groups", lambda self, user_id: {"user_id": user_id})
    async def get_assigned_groups(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get groups assigned to a user.
        
        For admin users, returns all groups.
        For other users, returns only their assigned groups.
        
        Args:
            user_id: ID of the user
            
        Returns:
            List of group dictionaries
        """
        # Get the user
        user = await self.get(PydanticObjectId(user_id))
        
        # For admin users, get all groups
        if user.role == UserRole.ADMIN:
            return await reference_manager.get_all_references("Group")
        
        # For other users, get their assigned groups
        if not user.assigned_groups:
            return []
        
        return await reference_manager.get_references_by_ids(
            reference_type="Group",
            reference_ids=user.assigned_groups
        )

    @handle_db_error("Failed to get accessible accounts", lambda self, user_id: {"user_id": user_id})
    async def get_accessible_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get accounts accessible to a user through their group assignments.
        
        Both VIEWER and EXPORTER roles can access accounts within their assigned groups.
        
        Args:
            user_id: ID of the user
            
        Returns:
            List of account dictionaries the user can access
        """
        # Get the user
        user = await self.get(PydanticObjectId(user_id))
        
        # Admin users can access all accounts
        if user.role == UserRole.ADMIN:
            return await reference_manager.get_all_references("Account")
        
        # If no assigned groups, no accessible accounts
        if not user.assigned_groups:
            return []
        
        # Get all accounts in the user's assigned groups
        # Both VIEWER and EXPORTER roles can access accounts in their groups
        accessible_accounts = []
        
        # For each group, get its accounts
        for group_id in user.assigned_groups:
            group = await reference_manager.get_reference(
                reference_id=group_id,
                reference_type="Group"
            )
            
            if group and "accounts" in group:
                # Get detailed account information for each account in this group
                group_accounts = await reference_manager.get_references_by_ids(
                    reference_type="Account",
                    reference_ids=group["accounts"]
                )
                
                accessible_accounts.extend(group_accounts)
        
        # Remove duplicates by ID
        seen_ids = set()
        unique_accounts = []
        
        for account in accessible_accounts:
            account_id = str(account.get("id"))
            if account_id not in seen_ids:
                seen_ids.add(account_id)
                unique_accounts.append(account)
        
        return unique_accounts

    @handle_db_error("Failed to check account access", lambda self, user_id, account_id: {"user_id": user_id, "account_id": account_id})
    async def check_account_access(
        self,
        user_id: str,
        account_id: str
    ) -> bool:
        """
        Check if a user has access to a specific account through group membership.
        
        Both VIEWER and EXPORTER roles can access accounts in their assigned groups.
        
        Args:
            user_id: ID of the user
            account_id: ID of the account
            
        Returns:
            Boolean indicating if the user has access to the account
        """
        # Get the user
        user = await self.get(PydanticObjectId(user_id))
        
        # Admin users can access all accounts
        if user.role == UserRole.ADMIN:
            return True
        
        # Both VIEWER and EXPORTER roles can access accounts in their groups
        # If no assigned groups, no access
        if not user.assigned_groups:
            return False
        
        # Check if the account is in any of the user's assigned groups
        for group_id in user.assigned_groups:
            group = await reference_manager.get_reference(
                reference_id=group_id,
                reference_type="Group"
            )
            
            if group and "accounts" in group and account_id in group["accounts"]:
                return True
        
        # Account not found in any of the user's groups
        return False

    @handle_db_error("Failed to check group access", lambda self, user_id, group_id: {"user_id": user_id, "group_id": group_id})
    async def check_group_access(
        self,
        user_id: str,
        group_id: str
    ) -> bool:
        """
        Check if a user has access to a specific group.
        
        Args:
            user_id: ID of the user
            group_id: ID of the group
            
        Returns:
            Boolean indicating if the user has access to the group
        """
        # Get the user
        user = await self.get(PydanticObjectId(user_id))
        
        # Admin users can access all groups
        if user.role == UserRole.ADMIN:
            return True
        
        # Other users can only access assigned groups
        return group_id in user.assigned_groups

    @handle_db_error("Failed to delete user", lambda self, id: {"user_id": str(id)})
    async def delete(self, id: PydanticObjectId) -> bool:
        """
        Delete a user.
        
        Args:
            id: ID of the user to delete
            
        Returns:
            True if deletion was successful
            
        Raises:
            ValidationError: If attempting to delete the last admin
        """
        # Get the user
        user = await self.get(id)
        
        # Check if this is the last admin
        if user.role == UserRole.ADMIN:
            admin_count = await User.find({"role": UserRole.ADMIN}).count()
            if admin_count <= 1:
                raise ValidationError(
                    "Cannot delete the last admin user",
                    context={"user_id": str(id)}
                )
        
        # Delete the user
        await user.delete()
        
        # Log the action
        logger.info(
            "Deleted user",
            extra={"user_id": str(id), "username": user.username}
        )
        
        return True


# Import service dependencies at the end to avoid circular imports
from app.services.reference.manager import reference_manager
from app.services.auth.password import password_manager

# Create a singleton instance for use throughout the application
user = CRUDUser(User)