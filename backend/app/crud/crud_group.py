"""
Account group CRUD operations with comprehensive error handling and service integration.

This module centralizes all database operations and external service integrations
for the AccountGroup model.
"""

import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.group import AccountGroup
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.references import UserRole
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

logger = get_logger(__name__)


class GroupCreate(BaseModel):
    """Schema for creating a new account group."""
    name: str = Field(..., min_length=3, max_length=32)
    description: Optional[str] = None
    accounts: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_group_name(cls, v: str) -> str:
        """Validate the group name."""
        name = v.strip()
        if not name:
            raise ValidationError("Group name cannot be empty", context={"name": v})
        if not all(c.isalnum() or c in "-_" for c in name):
            raise ValidationError(
                "Group name contains invalid characters",
                context={"name": name, "allowed": "alphanumeric, hyphen, underscore"}
            )
        return name


class GroupUpdate(BaseModel):
    """Schema for updating an existing account group."""
    name: Optional[str] = Field(None, min_length=3, max_length=32)
    description: Optional[str] = None
    accounts: Optional[List[str]] = None


class CRUDGroup(CRUDBase[AccountGroup, GroupCreate, GroupUpdate]):
    """
    CRUD operations for the AccountGroup model with enhanced validation and
    centralized service integration.
    """

    @handle_db_error("Failed to retrieve group by name", lambda self, name: {"name": name})
    async def get_by_name(self, name: str) -> AccountGroup:
        """Get a group by its name."""
        group = await AccountGroup.find_one({"name": name})
        if not group:
            raise NotFoundError("Group not found", context={"name": name})
        return group

    @handle_db_error("Failed to validate group name uniqueness", lambda self, name, exclude_id=None: {"name": name, "exclude_id": str(exclude_id) if exclude_id else None})
    async def validate_name_unique(
        self,
        name: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        """Check if a group name is unique."""
        query = {"name": name}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        return not await AccountGroup.find_one(query)

    @handle_db_error("Failed to validate user access", lambda self, group_id, user_id: {"group_id": str(group_id), "user_id": user_id})
    async def validate_user_access(
        self,
        group_id: PydanticObjectId, 
        user_id: str
    ) -> bool:
        """
        Check if a user has access to the specified group.
        
        Args:
            group_id: The group ObjectId
            user_id: The user ID
            
        Returns:
            True if the user has access, False otherwise
        """
        # Get user role
        user = await reference_manager.get_reference(
            reference_id=user_id,
            reference_type="User"
        )
        
        # Admin users have access to all groups
        if user and user.get("role") == UserRole.ADMIN:
            return True
            
        # Check for explicit access
        return await reference_manager.validate_access(
            user_id=user_id,
            resource_type="Group",
            resource_id=str(group_id)
        )

    @handle_db_error("Failed to validate accounts", lambda self, account_ids: {"account_ids": account_ids})
    async def validate_accounts(self, account_ids: List[str]) -> None:
        """Validate that all account IDs exist and are valid references."""
        # Check for duplicates
        if len(set(account_ids)) != len(account_ids):
            duplicates = set(
                acc for acc in account_ids if account_ids.count(acc) > 1
            )
            raise ValidationError("Duplicate account IDs", context={"duplicates": list(duplicates)})
        
        # Validate each account reference
        validations = await asyncio.gather(
            *[
                reference_manager.validate_reference(
                    source_type="Group",
                    target_type="Account",
                    reference_id=acc_id
                )
                for acc_id in account_ids
            ],
            return_exceptions=True
        )
        
        for acc_id, valid in zip(account_ids, validations):
            if isinstance(valid, Exception):
                raise valid
            if not valid:
                raise NotFoundError("Account not found", context={"account_id": acc_id})

    @handle_db_error("Failed to create group", lambda self, obj_in: {"name": obj_in.name})
    async def create(self, obj_in: GroupCreate) -> AccountGroup:
        """Create a new account group."""
        # Validate name uniqueness
        if not await self.validate_name_unique(obj_in.name):
            raise ValidationError("Group with this name already exists", context={"name": obj_in.name})
        
        # Validate accounts
        if obj_in.accounts:
            await self.validate_accounts(obj_in.accounts)
        
        # Create group
        group = AccountGroup(
            name=obj_in.name,
            description=obj_in.description,
            accounts=obj_in.accounts,
            created_at=datetime.utcnow(),
            modified_at=datetime.utcnow()
        )
        await group.save()
        
        # Add references
        if obj_in.accounts:
            tasks = [
                reference_manager.add_reference(
                    source_type="Group",
                    target_type="Account",
                    source_id=str(group.id),
                    target_id=account_id
                )
                for account_id in obj_in.accounts
            ]
            await asyncio.gather(*tasks)
        
        logger.info(
            "Created group",
            extra={
                "group_id": str(group.id),
                "name": group.name,
                "account_count": len(obj_in.accounts)
            }
        )
        return group

    @handle_db_error("Failed to update group", lambda self, id, obj_in: {"group_id": str(id), "fields": list(obj_in.model_dump(exclude_unset=True).keys()) if not isinstance(obj_in, dict) else list(obj_in.keys())})
    async def update(
        self,
        id: PydanticObjectId,
        obj_in: GroupUpdate
    ) -> AccountGroup:
        """Update an existing group."""
        group = await self.get(id)
        update_data = obj_in.model_dump(exclude_unset=True)
        
        # Validate name uniqueness if changing name
        if "name" in update_data and update_data["name"] != group.name:
            if not await self.validate_name_unique(update_data["name"], id):
                raise ValidationError("Group name already exists", context={"name": update_data["name"]})
        
        # Validate accounts if updating accounts list
        if "accounts" in update_data:
            await self.validate_accounts(update_data["accounts"])
            
            # Handle account reference updates
            old_accounts = set(group.accounts)
            new_accounts = set(update_data["accounts"])
            
            # Accounts to add
            accounts_to_add = new_accounts - old_accounts
            if accounts_to_add:
                tasks = [
                    reference_manager.add_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(id),
                        target_id=acc_id
                    )
                    for acc_id in accounts_to_add
                ]
                await asyncio.gather(*tasks)
            
            # Accounts to remove
            accounts_to_remove = old_accounts - new_accounts
            if accounts_to_remove:
                tasks = [
                    reference_manager.remove_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(id),
                        target_id=acc_id
                    )
                    for acc_id in accounts_to_remove
                ]
                await asyncio.gather(*tasks)
        
        # Update group
        group.update_from_dict(update_data)
        await group.save()
        
        logger.info(
            "Updated group",
            extra={
                "group_id": str(id),
                "fields": list(update_data.keys())
            }
        )
        return group

    @handle_db_error("Failed to add account to group", lambda self, group_id, account_id: {"group_id": str(group_id), "account_id": account_id})
    async def add_account(
        self,
        group_id: PydanticObjectId,
        account_id: str
    ) -> AccountGroup:
        """Add a single account to a group."""
        group = await self.get(group_id)
        
        # Check if account already in group
        if account_id in group.accounts:
            raise ValidationError("Account already in group", context={"account_id": account_id, "group_id": str(group_id)})
        
        # Validate account reference
        if not await reference_manager.validate_reference(
            source_type="Group",
            target_type="Account",
            reference_id=account_id
        ):
            raise NotFoundError("Account not found", context={"account_id": account_id})
        
        # Add account to group
        group.accounts.append(account_id)
        group.modified_at = datetime.utcnow()
        await group.save()
        
        # Add reference
        await reference_manager.add_reference(
            source_type="Group",
            target_type="Account",
            source_id=str(group_id),
            target_id=account_id
        )
        
        logger.info(
            "Added account to group",
            extra={"group_id": str(group_id), "account_id": account_id}
        )
        return group

    @handle_db_error("Failed to remove account from group", lambda self, group_id, account_id: {"group_id": str(group_id), "account_id": account_id})
    async def remove_account(
        self,
        group_id: PydanticObjectId,
        account_id: str
    ) -> AccountGroup:
        """Remove a single account from a group."""
        group = await self.get(group_id)
        
        # Check if account in group
        if account_id not in group.accounts:
            raise ValidationError("Account not in group", context={"account_id": account_id, "group_id": str(group_id)})
        
        # Remove account from group
        group.accounts.remove(account_id)
        group.modified_at = datetime.utcnow()
        await group.save()
        
        # Remove reference
        await reference_manager.remove_reference(
            source_type="Group",
            target_type="Account",
            source_id=str(group_id),
            target_id=account_id
        )
        
        logger.info(
            "Removed account from group",
            extra={"group_id": str(group_id), "account_id": account_id}
        )
        return group

    @handle_db_error("Failed to add accounts to group", lambda self, group_id, account_ids: {"group_id": str(group_id), "account_count": len(account_ids)})
    async def add_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> AccountGroup:
        """Add multiple accounts to a group."""
        group = await self.get(group_id)
        
        # Validate accounts
        await self.validate_accounts(account_ids)
        
        # Filter accounts not already in group
        new_accounts = [acc_id for acc_id in account_ids if acc_id not in group.accounts]
        if not new_accounts:
            return group
        
        # Add accounts to group
        group.accounts.extend(new_accounts)
        group.modified_at = datetime.utcnow()
        await group.save()
        
        # Add references
        tasks = [
            reference_manager.add_reference(
                source_type="Group",
                target_type="Account",
                source_id=str(group_id),
                target_id=acc_id
            )
            for acc_id in new_accounts
        ]
        await asyncio.gather(*tasks)
        
        logger.info(
            "Added accounts to group",
            extra={"group_id": str(group_id), "account_count": len(new_accounts)}
        )
        return group

    @handle_db_error("Failed to remove accounts from group", lambda self, group_id, account_ids: {"group_id": str(group_id), "account_count": len(account_ids)})
    async def remove_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> AccountGroup:
        """Remove multiple accounts from a group."""
        group = await self.get(group_id)
        
        # Filter accounts that are in the group
        accounts_to_remove = [acc_id for acc_id in account_ids if acc_id in group.accounts]
        if not accounts_to_remove:
            return group
        
        # Remove accounts from group
        for acc_id in accounts_to_remove:
            group.accounts.remove(acc_id)
        group.modified_at = datetime.utcnow()
        await group.save()
        
        # Remove references
        tasks = [
            reference_manager.remove_reference(
                source_type="Group",
                target_type="Account",
                source_id=str(group_id),
                target_id=acc_id
            )
            for acc_id in accounts_to_remove
        ]
        await asyncio.gather(*tasks)
        
        logger.info(
            "Removed accounts from group",
            extra={"group_id": str(group_id), "account_count": len(accounts_to_remove)}
        )
        return group

    @handle_db_error("Failed to sync group balances", lambda self, group_id: {"group_id": str(group_id)})
    async def sync_balances(
        self,
        group_id: PydanticObjectId
    ) -> Dict[str, Any]:
        """Synchronize account balances and update group metrics."""
        group = await self.get(group_id)
        results = []
        total_balance = 0.0
        total_equity = 0.0
        active_count = 0
        error_count = 0
        
        # Get trading service
        trading_service = await reference_manager.get_service("TradingService")
        
        # Process each account
        for account_id in group.accounts:
            try:
                balance_info = await trading_service.get_account_balance(account_id=account_id)
                total_balance += float(balance_info["balance"])
                total_equity += float(balance_info["equity"])
                if balance_info.get("is_active", False):
                    active_count += 1
                results.append({
                    "account_id": account_id,
                    "success": True,
                    "balance": float(balance_info["balance"]),
                    "equity": float(balance_info["equity"])
                })
            except Exception as e:
                error_count += 1
                results.append({
                    "account_id": account_id,
                    "success": False,
                    "error": str(e)
                })
        
        # Update error tracking
        if error_count > 0:
            group.error_count += 1
            group.error_timestamps.append(datetime.utcnow())
            group.error_timestamps = group.error_timestamps[-10:]  # Keep last 10 errors
            group.last_error = f"Failed to sync {error_count} accounts"
        else:
            group.error_count = 0
            group.error_timestamps = []
            group.last_error = None
        
        # Update group metrics
        group.total_balance = total_balance
        group.total_equity = total_equity
        group.active_accounts = active_count
        group.last_sync = datetime.utcnow()
        group.modified_at = datetime.utcnow()
        await group.save()
        
        return {
            "success": error_count == 0,
            "total_balance": total_balance,
            "total_equity": total_equity,
            "active_accounts": active_count,
            "error_count": error_count,
            "results": results
        }

    @handle_db_error("Failed to verify WebSocket health", lambda self, group_id: {"group_id": str(group_id)})
    async def verify_websocket_health(
        self,
        group_id: PydanticObjectId
    ) -> Dict[str, Any]:
        """Verify WebSocket connections for all accounts in the group."""
        group = await self.get(group_id)
        results = {}
        active_connections = 0
        
        # Check each account's WebSocket connection
        for account_id in group.accounts:
            try:
                ws_status = await ws_manager.get_connection_status(account_id)
                if ws_status.get("connected", False):
                    active_connections += 1
                results[account_id] = ws_status
            except Exception as e:
                results[account_id] = {"connected": False, "error": str(e)}
        
        # Update group status
        group.ws_connections = active_connections
        group.last_ws_check = datetime.utcnow()
        group.modified_at = datetime.utcnow()
        await group.save()
        
        return {
            "active_connections": active_connections,
            "total_accounts": len(group.accounts),
            "results": results,
            "timestamp": datetime.utcnow().isoformat()
        }

    @handle_db_error("Failed to get group performance", lambda self, group_id, start_date, end_date: {"group_id": str(group_id), "date_range": f"{start_date} to {end_date}"})
    async def get_performance(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Get daily performance metrics for a date range."""
        group = await self.get(group_id)
        performance = await DailyPerformance.get_aggregated_performance(
            account_ids=group.accounts,
            start_date=start_date,
            end_date=end_date
        )
        return performance

    @handle_db_error("Failed to get period statistics", lambda self, group_id, start_date, end_date: {"group_id": str(group_id), "date_range": f"{start_date} to {end_date}"})
    async def get_period_stats(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """Get aggregated statistics for a date range."""
        group = await self.get(group_id)
        stats = await DailyPerformance.get_period_statistics(
            account_ids=group.accounts,
            start_date=start_date,
            end_date=end_date
        )
        return stats

    @handle_db_error("Failed to delete group", lambda self, id: {"group_id": str(id)})
    async def delete(self, id: PydanticObjectId) -> bool:
        """Delete a group and clean up references."""
        group = await self.get(id)
        
        # Clean up references
        if group.accounts:
            tasks = [
                reference_manager.remove_reference(
                    source_type="Group",
                    target_type="Account",
                    source_id=str(id),
                    target_id=acc_id
                )
                for acc_id in group.accounts
            ]
            await asyncio.gather(*tasks)
        
        # Delete group
        await group.delete()
        
        logger.info("Deleted group", extra={"group_id": str(id)})
        return True


# Import external services
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager

# Create singleton instance
group = CRUDGroup(AccountGroup)