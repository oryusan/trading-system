"""
Account group model with standardized validation and clear responsibilities.

This model focuses on data structure, internal validation, and core business rules
without direct external service integration.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import ValidationError
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class AccountGroup(Document):
    """
    Represents a grouping of trading accounts.
    
    This model maintains the data structure and core validation rules
    but delegates complex operations to the CRUD layer.
    """
    # Core fields
    name: Indexed(str, unique=True) = Field(
        ..., 
        description="Unique group name"
    )
    description: Optional[str] = Field(
        None,
        description="Optional group description"
    )
    accounts: List[str] = Field(
        default_factory=list,
        description="List of account IDs in this group"
    )

    # Quick access metrics (updated by CRUD operations)
    total_balance: float = Field(
        0.0,
        description="Total balance across accounts",
        ge=0
    )
    total_equity: float = Field(
        0.0,
        description="Total equity across accounts",
        ge=0
    )
    active_accounts: int = Field(
        0,
        description="Number of active accounts",
        ge=0
    )

    # WebSocket tracking (updated by CRUD operations)
    ws_connections: int = Field(
        0,
        description="Active WebSocket connections"
    )
    last_ws_check: Optional[datetime] = Field(
        None,
        description="Last WebSocket health check"
    )

    # Error tracking
    error_count: int = Field(
        0,
        description="Consecutive error count"
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )
    error_timestamps: List[datetime] = Field(
        default_factory=list,
        description="Recent error timestamps"
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Group creation timestamp" 
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification timestamp"
    )
    last_sync: Optional[datetime] = Field(
        None,
        description="Last balance sync timestamp"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "account_groups"
        indexes = [
            "name",
            "accounts",
            "created_at",
            [("name", 1), ("accounts", 1)],
            [("error_count", 1), ("last_sync", 1)]
        ]

    @field_validator("name")
    @classmethod
    def validate_group_name(cls, v: str) -> str:
        """Validate and normalize the group name."""
        name = v.strip()
        if not name:
            raise ValidationError("Group name cannot be empty", context={"name": v})
        if len(name) < 3:
            raise ValidationError("Group name too short", context={"name": name, "min_length": 3})
        if len(name) > 32:
            raise ValidationError("Group name too long", context={"name": name, "max_length": 32})
        if not all(c.isalnum() or c in "-_" for c in name):
            raise ValidationError(
                "Group name contains invalid characters",
                context={"name": name, "allowed": "alphanumeric, hyphen, underscore"}
            )
        return name

    @before_event([Replace, Insert])
    async def pre_save_hooks(self):
        """Perform validation before saving the document.
        
        Note: Complex validation that requires external services should be
        handled in the CRUD layer, not in the model.
        """
        # Check for duplicate accounts
        if len(set(self.accounts)) != len(self.accounts):
            duplicates = set(
                acc for acc in self.accounts if self.accounts.count(acc) > 1
            )
            raise ValidationError(
                "Duplicate account references",
                context={"duplicates": list(duplicates)}
            )
        
        # Update modified_at timestamp
        self.modified_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert the group to a dictionary representation."""
        return {
            "group_info": {
                "id": str(self.id),
                "name": self.name,
                "description": self.description,
                "accounts": self.accounts
            },
            "balances": {
                "total_balance": self.total_balance,
                "total_equity": self.total_equity,
                "active_accounts": self.active_accounts
            },
            "websocket": {
                "ws_connections": self.ws_connections,
                "last_ws_check": self.last_ws_check.isoformat() if self.last_ws_check else None
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error
            }
        }

    def __repr__(self) -> str:
        """String representation of the group."""
        return f"AccountGroup(name='{self.name}', accounts={len(self.accounts)})"

    def update_from_dict(self, update_data: Dict[str, Any]) -> None:
        """Update fields from a dictionary of values."""
        for field, value in update_data.items():
            if hasattr(self, field):
                setattr(self, field, value)
        self.modified_at = datetime.utcnow()