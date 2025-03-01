"""
User model with clear separation of concerns and group-based access model.

This model focuses purely on data structure and validation,
with no direct service integration or external dependencies.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Set

from beanie import Document, Indexed
from pydantic import Field, field_validator, ConfigDict

# Core imports only
from app.core.errors.base import ValidationError
from app.core.references import UserRole, ModelState

class User(Document):
    """
    User model representing core user data structure and validation rules.
    
    Access model:
    - ADMIN users have access to everything
    - VIEWER users are assigned to groups and access accounts within those groups
    - EXPORTER users are assigned to groups for export permissions
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

    # Resource assignments - both roles can be assigned to groups
    assigned_groups: List[str] = Field(
        default_factory=list,
        description="Group IDs the user can access"
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

    # Model configuration
    model_config = ConfigDict(
        validate_assignment=True,
    )

    class Settings:
        """Collection settings and indexes."""
        name = "users"
        indexes = [
            "username",
            "role",
            "created_at",
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

    def is_locked_out(self) -> bool:
        """
        Determine if the user is locked out based on failed login attempts.
        
        A user is locked out if they have 5 or more failed attempts within 30 minutes.
        """
        if self.login_attempts >= 5 and self.last_failed_login:
            if datetime.utcnow() - self.last_failed_login < timedelta(minutes=30):
                return True
        return False

    def has_resource_access(self, resource_type: str, resource_id: str) -> bool:
        """
        Check if the user has access to the given resource based on role and assignments.
        
        This is a pure business rule method with no external dependencies.

        Args:
            resource_type: "account" or "group"
            resource_id: ID of the resource to check access for

        Returns:
            Boolean indicating if user has access
        """
        # Admin users have access to everything
        if self.role == UserRole.ADMIN:
            return True
            
        # All users can access their assigned groups
        if resource_type == "group":
            return resource_id in self.assigned_groups
            
        # For account access, we need to check through the CRUD layer
        # since it depends on group membership - both VIEWER and EXPORTER
        # roles can access accounts in their assigned groups
        return False  # This is determined at the CRUD layer

    def get_user_info(self) -> Dict[str, Any]:
        """
        Return a dictionary with user info for external consumption.
        Pure transformation method with no dependencies.
        """
        return {
            "id": str(self.id),
            "username": self.username,
            "role": self.role.value,  # Convert enum to string value
            "is_active": self.is_active,
            "assignments": {
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
        Pure transformation method with no dependencies.
        """
        return {
            "id": str(self.id),
            "username": self.username,
            "role": self.role.value,
            "is_active": self.is_active,
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