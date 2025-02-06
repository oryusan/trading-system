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
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors import (
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
        """Validate username format."""
        if not v or not v.strip():
            raise ValidationError(
                "Username cannot be empty",
                context={"username": v}
            )

        username = v.strip().lower()
        
        if len(username) < 3 or len(username) > 32:
            raise ValidationError(
                "Username must be between 3 and 32 characters",
                context={
                    "username": username,
                    "length": len(username)
                }
            )

        if not username.isalnum():
            raise ValidationError(
                "Username can only contain letters and numbers",
                context={"username": username}
            )

        return username

    @field_validator("role")
    @classmethod
    def validate_role_assignments(cls, v: UserRole, info: Dict) -> UserRole:
        """Validate role and assignment compatibility."""
        try:
            assigned_accounts = info.data.get("assigned_accounts", [])
            assigned_groups = info.data.get("assigned_groups", [])

            if v == UserRole.VIEWER and assigned_groups:
                raise ValidationError(
                    "Viewer users cannot have assigned groups",
                    context={
                        "role": v,
                        "assigned_groups": assigned_groups
                    }
                )

            if v == UserRole.EXPORTER and assigned_accounts:
                raise ValidationError(
                    "Exporter users cannot have assigned accounts",
                    context={
                        "role": v,
                        "assigned_accounts": assigned_accounts
                    }
                )

            return v

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Role validation failed",
                context={
                    "role": v,
                    "error": str(e)
                }
            )

    @before_event([Replace, Insert])
    async def validate_references(self):
        """Validate all references using reference manager."""
        try:
            seen_accounts = set()
            seen_groups = set()

            # Validate accounts
            for account_id in self.assigned_accounts:
                if account_id in seen_accounts:
                    raise ValidationError(
                        "Duplicate account assignment",
                        context={"account_id": account_id}
                    )
                seen_accounts.add(account_id)

                valid = await reference_manager.validate_reference(
                    source_type="User",
                    target_type="Account",
                    reference_id=account_id
                )
                if not valid:
                    raise NotFoundError(
                        "Referenced account not found",
                        context={"account_id": account_id}
                    )

            # Validate groups
            for group_id in self.assigned_groups:
                if group_id in seen_groups:
                    raise ValidationError(
                        "Duplicate group assignment",
                        context={"group_id": group_id}
                    )
                seen_groups.add(group_id)

                valid = await reference_manager.validate_reference(
                    source_type="User",
                    target_type="Group",
                    reference_id=group_id
                )
                if not valid:
                    raise NotFoundError(
                        "Referenced group not found",
                        context={"group_id": group_id}
                    )

            self.modified_at = datetime.utcnow()

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "username": self.username,
                    "error": str(e)
                }
            )

    async def validate_access(
        self,
        resource_type: str,
        resource_id: str
    ) -> bool:
        """Validate user access to resource."""
        try:
            if self.role == UserRole.ADMIN:
                return True

            if resource_type == "account":
                if self.role != UserRole.VIEWER:
                    return False
                return resource_id in self.assigned_accounts

            if resource_type == "group":
                if self.role != UserRole.EXPORTER:
                    return False
                return resource_id in self.assigned_groups

            raise ValidationError(
                "Invalid resource type",
                context={
                    "type": resource_type,
                    "valid_types": ["account", "group"]
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Access validation failed",
                context={
                    "username": self.username,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "error": str(e)
                }
            )

    async def record_login_attempt(
        self,
        success: bool,
        ip_address: Optional[str] = None
    ) -> None:
        """Record login attempt result."""
        try:
            if success:
                self.last_login = datetime.utcnow()
                self.login_attempts = 0
                self.last_failed_login = None

                logger.info(
                    "Successful login",
                    extra={
                        "username": self.username,
                        "ip_address": ip_address
                    }
                )
            else:
                self.login_attempts += 1
                self.last_failed_login = datetime.utcnow()

                logger.warning(
                    "Failed login attempt",
                    extra={
                        "username": self.username,
                        "attempts": self.login_attempts,
                        "ip_address": ip_address
                    }
                )

            self.modified_at = datetime.utcnow()
            await self.save()

        except Exception as e:
            raise DatabaseError(
                "Failed to record login attempt",
                context={
                    "username": self.username,
                    "success": success,
                    "error": str(e)
                }
            )

    async def is_locked_out(self) -> bool:
        """Check if user is locked out from failed attempts."""
        if self.login_attempts >= 5 and self.last_failed_login:
            lockout_window = timedelta(minutes=30)
            time_since_failure = datetime.utcnow() - self.last_failed_login
            return time_since_failure < lockout_window
        return False

    def get_user_info(self) -> Dict[str, Any]:
        """Get user information in standardized format."""
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
        """Convert user to dictionary format."""
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
        """String representation."""
        return (
            f"User(username='{self.username}', "
            f"role={self.role}, active={self.is_active})"
        )

# Import at end to avoid circular dependencies
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)