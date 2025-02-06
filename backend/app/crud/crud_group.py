"""
Enhanced account group CRUD operations with error handling integration.

Features:
- Validation for name uniqueness and account existence
- Account reference management
- Performance data aggregation
- Comprehensive error handling and logging
"""

from typing import List, Optional, Dict
from datetime import datetime
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator

from app.crud.base import CRUDBase
from app.models.group import AccountGroup
from app.models.account import Account
from app.models.daily_performance import DailyPerformance
from app.core.errors import (
    DatabaseError,
    ValidationError,
    NotFoundError
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

class GroupCreate(BaseModel):
    """
    Schema for creating a new account group.
    
    Attributes:
        name: Unique name for the group
        description: Optional description text
        accounts: Initial list of account IDs to assign
    """
    name: str
    description: Optional[str] = None
    accounts: List[str] = []

    @field_validator("name")
    @classmethod
    def validate_group_name(cls, v: str) -> str:
        """Ensure group name is not empty."""
        if not v.strip():
            raise ValidationError(
                "Group name cannot be empty",
                context={"name": v}
            )
        return v.strip()

class GroupUpdate(BaseModel):
    """
    Schema for updating an existing account group.
    
    Attributes:
        name: New name for the group (optional)
        description: New description (optional)
        accounts: Updated list of account IDs (optional)
    """
    name: Optional[str] = None
    description: Optional[str] = None
    accounts: Optional[List[str]] = None

class CRUDGroup(CRUDBase[AccountGroup, GroupCreate, GroupUpdate]):
    """
    CRUD operations for AccountGroup model with enhanced validation.
    
    Features:
    - Validate group name uniqueness
    - Manage account assignments and references
    - Track group performance
    - Handle error cases with context
    """

    async def get_by_name(self, name: str) -> Optional[AccountGroup]:
        """
        Get group by its unique name.
        
        Args:
            name: The group name to search for
        
        Returns:
            AccountGroup: The found group document
        
        Raises:
            NotFoundError: If no group found with given name
            DatabaseError: If database operation fails
        """
        try:
            group = await AccountGroup.find_one({"name": name})
            if not group:
                raise NotFoundError(
                    "Group not found",
                    context={"name": name}
                )
            return group
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve group by name",
                context={
                    "name": name,
                    "error": str(e)
                }
            )

    async def validate_name_unique(
        self,
        name: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        """
        Check if group name is unique.
        
        Args:
            name: The group name to validate
            exclude_id: Optional group ID to exclude from check
        
        Returns:
            bool: True if name is unique, False otherwise
        
        Raises:
            DatabaseError: If validation fails
        """
        try:
            query = {"name": name}
            if exclude_id:
                query["_id"] = {"$ne": exclude_id}
            return not await self.exists(query)
        except Exception as e:
            raise DatabaseError(
                "Failed to validate group name uniqueness",
                context={
                    "name": name,
                    "exclude_id": str(exclude_id) if exclude_id else None,
                    "error": str(e)
                }
            )

    async def validate_accounts(self, account_ids: List[str]) -> None:
        """
        Validate that all accounts exist.
        
        Args:
            account_ids: List of account IDs to validate
        
        Raises:
            NotFoundError: If any account doesn't exist
            DatabaseError: If validation fails
        """
        try:
            for account_id in account_ids:
                # Use reference manager for validation
                valid = await reference_manager.validate_reference(
                    source_type="Group",
                    target_type="Account",
                    reference_id=account_id
                )
                if not valid:
                    raise NotFoundError(
                        "Account not found",
                        context={"account_id": account_id}
                    )
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to validate accounts",
                context={
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def create(self, obj_in: GroupCreate) -> AccountGroup:
        """
        Create new group with account validation.
        
        Args:
            obj_in: The group creation schema
        
        Returns:
            AccountGroup: The created group document
        
        Raises:
            ValidationError: If group name not unique
            NotFoundError: If accounts don't exist
            DatabaseError: If creation fails
        """
        try:
            if not await self.validate_name_unique(obj_in.name):
                raise ValidationError(
                    "Group with this name already exists",
                    context={"name": obj_in.name}
                )

            await self.validate_accounts(obj_in.accounts)

            group = await super().create(obj_in)

            # Add group to account references
            for account_id in obj_in.accounts:
                await reference_manager.add_reference(
                    source_type="Group",
                    target_type="Account",
                    source_id=str(group.id),
                    target_id=account_id
                )

            logger.info(
                "Created group",
                extra={
                    "group_id": str(group.id),
                    "name": group.name,
                    "account_count": len(obj_in.accounts)
                }
            )

            return group

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to create group",
                context={
                    "name": obj_in.name,
                    "error": str(e)
                }
            )

    async def add_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> AccountGroup:
        """
        Add accounts to group.
        
        Args:
            group_id: The target group ID
            account_ids: List of account IDs to add
        
        Returns:
            AccountGroup: The updated group document
        
        Raises:
            NotFoundError: If group or accounts not found
            DatabaseError: If operation fails
        """
        try:
            group = await self.get(group_id)
            await self.validate_accounts(account_ids)

            # Update group-account references
            for account_id in account_ids:
                if account_id not in group.accounts:
                    group.accounts.append(account_id)
                    await reference_manager.add_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(group_id),
                        target_id=account_id
                    )

            await group.save()

            logger.info(
                "Added accounts to group",
                extra={
                    "group_id": str(group_id),
                    "account_count": len(account_ids)
                }
            )

            return group

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to add accounts",
                context={
                    "group_id": str(group_id),
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def remove_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> AccountGroup:
        """
        Remove accounts from group.
        
        Args:
            group_id: The group to remove from
            account_ids: List of account IDs to remove
        
        Returns:
            AccountGroup: The updated group document
        
        Raises:
            NotFoundError: If group not found
            DatabaseError: If operation fails
        """
        try:
            group = await self.get(group_id)

            # Update references
            for account_id in account_ids:
                if account_id in group.accounts:
                    group.accounts.remove(account_id)
                    await reference_manager.remove_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(group_id),
                        target_id=account_id
                    )

            await group.save()

            logger.info(
                "Removed accounts from group",
                extra={
                    "group_id": str(group_id),
                    "account_count": len(account_ids)
                }
            )

            return group

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to remove accounts",
                context={
                    "group_id": str(group_id),
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def get_performance(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """
        Get group performance data.
        
        Args:
            group_id: The group to get performance for
            start_date: Start date for metrics (YYYY-MM-DD)
            end_date: End date for metrics (YYYY-MM-DD)
        
        Returns:
            List[Dict]: Daily performance metrics
        
        Raises:
            NotFoundError: If group not found
            DatabaseError: If operation fails
        """
        try:
            group = await self.get(group_id)
            return await DailyPerformance.get_aggregated_performance(
                account_ids=group.accounts,
                start_date=start_date,
                end_date=end_date
            )
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get group performance",
                context={
                    "group_id": str(group_id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def get_period_stats(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get aggregated performance stats for a time period.
        
        Args:
            group_id: The group to get statistics for
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        
        Returns:
            Dict: Period statistics including total PnL, trade count, etc.
        
        Raises:
            NotFoundError: If group not found
            DatabaseError: If operation fails
        """
        try:
            group = await self.get(group_id)
            return await DailyPerformance.get_period_statistics(
                account_ids=group.accounts,
                start_date=start_date,
                end_date=end_date
            )
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get period statistics",
                context={
                    "group_id": str(group_id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

# Import at end to avoid circular imports
from app.services.reference.manager import reference_manager

# Create singleton instance
group = CRUDGroup(AccountGroup)