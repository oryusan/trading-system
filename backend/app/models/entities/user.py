"""
Enhanced user model with proper error handling and service integration.

Features:
- Role-based access control
- Reference validation using reference manager
- Proper error context and logging
- Service layer integration
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import asyncio

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import (
    ValidationError,
    DatabaseError, 
    NotFoundError,
    AuthenticationError
)
from app.core.references import (
    UserRole,
    ModelState,
    ValidationResult
)


class User(Document):
    """
    User model with enhanced validation and service integration.
    
    Features:
    - Role-based access
    - Resource assignments 
    - Security tracking
    - Reference validation
    """

    # Core fields
    username: Indexed(str, unique=True) = Field(
        ...,
        description="Unique username assigned by admin"
    )
    hashed_password: str = Field(
        ..., 
        description="Hashed password for authentication"
    )
    role: UserRole = Field(
        ...,
        description="User role (admin/exporter/viewer)"
    )
    is_active: bool = Field(
        True,
        description="Whether user account is active"
    )

    # Resource assignments
    assigned_accounts: List[str] = Field(
        default_factory=list,
        description="Account IDs viewer can access"
    )
    assigned_groups: List[str] = Field(
        default_factory=list,
        description="Group IDs exporter can access"
    )

    # Audit fields
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When user was created"
    )
    created_by: Optional[str] = Field(
        None,
        description="Who created this user"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification time"
    )
    modified_by: Optional[str] = Field(
        None,
        description="Who last modified user"
    )

    # Security tracking
    last_login: Optional[datetime] = Field(
        None,
        description="Last successful login"
    )
    login_attempts: int = Field(
        0,
        description="Failed login attempts"
    )
    last_failed_login: Optional[datetime] = Field(
        None,
        description="Last failed login time"  
    )
    password_changed_at: Optional[datetime] = Field(
        None,
        description="Last password change"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "users"
        indexes = [
            "username",
            "role",
            "created_at",
            "assigned_accounts",
            "assigned_groups"
        ]

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Validate and normalize username."""
        if not v or not v.strip():
            raise ValidationError(
                "Username cannot be empty",
                context={"username": v}
            )
        username = v.strip().lower()
        if not (3 <= len(username) <= 32):
            raise ValidationError(
                "Username must be between 3 and 32 characters",
                context={"username": username, "length": len(username)}
            )
        if not username.isalnum():
            raise ValidationError(
                "Username can only contain letters and numbers",
                context={"username": username}
            )
        return username

    @field_validator("role")
    @classmethod
    def validate_role_assignments(cls, v: UserRole, info) -> UserRole:
        """
        Ensure role is compatible with resource assignments.
        
        - Viewer users should not have assigned groups.
        - Exporter users should not have assigned accounts.
        """
        assigned_accounts = info.data.get("assigned_accounts", [])
        assigned_groups = info.data.get("assigned_groups", [])
        if v == UserRole.VIEWER and assigned_groups:
            raise ValidationError(
                "Viewer users cannot have assigned groups",
                context={"role": v, "assigned_groups": assigned_groups}
            )
        if v == UserRole.EXPORTER and assigned_accounts:
            raise ValidationError(
                "Exporter users cannot have assigned accounts",
                context={"role": v, "assigned_accounts": assigned_accounts}
            )
        return v

    async def _validate_reference(self, reference_id: str, source_type: str, target_type: str) -> None:
        """
        Helper method to validate a single reference.
        
        Raises:
            NotFoundError: if the reference is not valid.
        """
        valid = await reference_manager.validate_reference(
            source_type=source_type,
            target_type=target_type,
            reference_id=reference_id
        )
        if not valid:
            error_message = f"Referenced {target_type.lower()} not found"
            context = {f"{target_type.lower()}_id": reference_id}
            raise NotFoundError(error_message, context=context)

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate all assigned account and group references.
        
        - Checks for duplicate assignments.
        - Validates each reference concurrently.
        - Updates the modification time.
        """
        try:
            # Check for duplicate assignments
            if len(self.assigned_accounts) != len(set(self.assigned_accounts)):
                duplicates = set(
                    a for a in self.assigned_accounts if self.assigned_accounts.count(a) > 1
                )
                raise ValidationError(
                    "Duplicate account assignments found",
                    context={"duplicates": list(duplicates)}
                )
            if len(self.assigned_groups) != len(set(self.assigned_groups)):
                duplicates = set(
                    g for g in self.assigned_groups if self.assigned_groups.count(g) > 1
                )
                raise ValidationError(
                    "Duplicate group assignments found",
                    context={"duplicates": list(duplicates)}
                )

            # Validate accounts and groups concurrently
            account_tasks = [
                self._validate_reference(account_id, "User", "Account")
                for account_id in self.assigned_accounts
            ]
            group_tasks = [
                self._validate_reference(group_id, "User", "Group")
                for group_id in self.assigned_groups
            ]
            await asyncio.gather(*account_tasks, *group_tasks)
            self.modified_at = datetime.utcnow()
        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={"username": self.username, "error": str(e)}
            ) from e

    async def validate_access(self, resource_type: str, resource_id: str) -> bool:
        """
        Check if the user has access to the given resource.

        Admin users have access to all resources.
        Viewers can access accounts and Exporters can access groups.

        Args:
            resource_type: "account" or "group".
            resource_id: Identifier of the resource.

        Returns:
            True if access is granted, otherwise False.

        Raises:
            ValidationError: If the resource_type is invalid.
        """
        if self.role == UserRole.ADMIN:
            return True

        valid_resource_types = {"account", "group"}
        if resource_type not in valid_resource_types:
            raise ValidationError(
                "Invalid resource type",
                context={"type": resource_type, "valid_types": list(valid_resource_types)}
            )

        role_mapping = {
            "account": (UserRole.VIEWER, self.assigned_accounts),
            "group": (UserRole.EXPORTER, self.assigned_groups)
        }
        expected_role, assignments = role_mapping[resource_type]
        return self.role == expected_role and resource_id in assignments

    async def record_login_attempt(self, success: bool, ip_address: Optional[str] = None) -> None:
        """
        Record a login attempt, update security fields and log the event.

        On success, resets the failed login count and updates last_login.
        On failure, increments the login_attempts and updates last_failed_login.
        """
        try:
            now = datetime.utcnow()
            if success:
                self.last_login = now
                self.login_attempts = 0
                self.last_failed_login = None
                logger.info(
                    "Successful login",
                    extra={"username": self.username, "ip_address": ip_address}
                )
            else:
                self.login_attempts += 1
                self.last_failed_login = now
                logger.warning(
                    "Failed login attempt",
                    extra={
                        "username": self.username,
                        "attempts": self.login_attempts,
                        "ip_address": ip_address
                    }
                )
            self.modified_at = now
            await self.save()
        except Exception as e:
            raise DatabaseError(
                "Failed to record login attempt",
                context={"username": self.username, "success": success, "error": str(e)}
            ) from e

    async def is_locked_out(self) -> bool:
        """
        Determine if the user is locked out based on failed login attempts.
        
        A user is locked out if they have 5 or more failed attempts within 30 minutes.
        """
        if self.login_attempts >= 5 and self.last_failed_login:
            if datetime.utcnow() - self.last_failed_login < timedelta(minutes=30):
                return True
        return False

    def get_user_info(self) -> Dict[str, Any]:
        """
        Return a dictionary with user info for external consumption.
        """
        return {
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "assignments": {
                "accounts": self.assigned_accounts,
                "groups": self.assigned_groups
            },
            "audit": {
                "created_at": self.created_at.isoformat(),
                "created_by": self.created_by,
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "modified_by": self.modified_by
            },
            "security": {
                "last_login": self.last_login.isoformat() if self.last_login else None,
                "login_attempts": self.login_attempts,
                "last_failed_login": self.last_failed_login.isoformat() if self.last_failed_login else None,
                "password_changed_at": self.password_changed_at.isoformat() if self.password_changed_at else None
            }
        }

    def to_dict(self) -> ModelState:
        """
        Convert the user model to a dictionary for internal state representation.
        """
        return {
            "username": self.username,
            "role": self.role.value,
            "is_active": self.is_active,
            "assigned_accounts": self.assigned_accounts,
            "assigned_groups": self.assigned_groups,
            "metadata": {
                "created_at": self.created_at.isoformat(),
                "created_by": self.created_by,
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "modified_by": self.modified_by
            },
            "login_info": {
                "last_login": self.last_login.isoformat() if self.last_login else None,
                "login_attempts": self.login_attempts,
                "last_failed": self.last_failed_login.isoformat() if self.last_failed_login else None
            }
        }

    def __repr__(self) -> str:
        """Return a string representation of the user."""
        return f"User(username='{self.username}', role={self.role}, active={self.is_active})"


# Import at end to avoid circular dependencies
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)
