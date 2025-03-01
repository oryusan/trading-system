"""
Account group CRUD operations with centralized service integration.

This module provides comprehensive operations for managing account groups,
including group creation, account management, performance tracking, and exports.
"""

import io
import csv
from typing import List, Optional, Dict, Any, Union, Tuple
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.group import AccountGroup
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

# Try to import xlsxwriter for Excel exports, with fallback to csv-only if not available
try:
    import xlsxwriter
    EXCEL_SUPPORT = True
except ImportError:
    EXCEL_SUPPORT = False

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
    name: Optional[str] = None
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
        existing_group = await AccountGroup.find_one(query)
        return existing_group is None

    async def validate_account_references(self, account_ids: List[str]) -> None:
        """
        Validate that account references exist.
        
        Args:
            account_ids: List of account IDs to validate
        
        Raises:
            ValidationError: If there are duplicates
            NotFoundError: If a reference doesn't exist
        """
        # Check for duplicates
        if len(set(account_ids)) != len(account_ids):
            duplicates = [acc for acc in set(account_ids) if account_ids.count(acc) > 1]
            raise ValidationError(
                "Duplicate account assignments",
                context={"duplicates": duplicates}
            )
        
        # Validate all account references concurrently
        tasks = [self._validate_account_reference(account_id) for account_id in account_ids]
                
        if tasks:
            await asyncio.gather(*tasks)

    async def _validate_account_reference(self, account_id: str) -> None:
        """
        Validate a single account reference to ensure it exists.
        
        Args:
            account_id: ID of the account to check
            
        Raises:
            NotFoundError: If the reference doesn't exist
        """
        valid = await reference_manager.validate_reference(
            source_type="Group",
            target_type="Account",
            reference_id=account_id
        )
        
        if not valid:
            raise NotFoundError(
                "Referenced account not found",
                context={"account_id": account_id}
            )

    @handle_db_error("Failed to create group", lambda self, obj_in: {"name": obj_in.name})
    async def create(self, obj_in: GroupCreate) -> AccountGroup:
        """
        Create a new account group with validation.
        """
        # Validate name uniqueness
        if not await self.validate_name_unique(obj_in.name):
            raise ValidationError(
                "Group with this name already exists",
                context={"name": obj_in.name}
            )
        
        # Validate account references
        if obj_in.accounts:
            await self.validate_account_references(obj_in.accounts)
        
        # Create group object
        group = AccountGroup(
            name=obj_in.name,
            description=obj_in.description,
            accounts=obj_in.accounts,
            created_at=datetime.utcnow(),
            modified_at=datetime.utcnow()
        )
        
        # Save to database
        await group.save()
        
        # Add reference connections if there are accounts
        if obj_in.accounts:
            for account_id in obj_in.accounts:
                await reference_manager.add_reference(
                    source_type="Group",
                    target_type="Account",
                    source_id=str(group.id),
                    target_id=account_id
                )
        
        # Log the action
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
        obj_in: Union[GroupUpdate, Dict[str, Any]]
    ) -> AccountGroup:
        """
        Update an existing group with validation.
        """
        # Get current group
        group = await self.get(id)
        
        # Convert input to dictionary if needed
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        
        # Validate name uniqueness if changing name
        if "name" in update_data and update_data["name"] != group.name:
            if not await self.validate_name_unique(update_data["name"], id):
                raise ValidationError(
                    "Group name already exists",
                    context={"name": update_data["name"]}
                )
        
        # Validate account references if updating accounts
        if "accounts" in update_data and update_data["accounts"] is not None:
            await self.validate_account_references(update_data["accounts"])
            
            # Handle reference updates
            old_accounts = set(group.accounts)
            new_accounts = set(update_data["accounts"])
            
            # Accounts to add
            accounts_to_add = new_accounts - old_accounts
            if accounts_to_add:
                for account_id in accounts_to_add:
                    await reference_manager.add_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(id),
                        target_id=account_id
                    )
            
            # Accounts to remove
            accounts_to_remove = old_accounts - new_accounts
            if accounts_to_remove:
                for account_id in accounts_to_remove:
                    await reference_manager.remove_reference(
                        source_type="Group",
                        target_type="Account",
                        source_id=str(id),
                        target_id=account_id
                    )
        
        # Update all fields
        for field, value in update_data.items():
            setattr(group, field, value)
        
        # Set modified timestamp
        group.modified_at = datetime.utcnow()
        
        # Save the updated group
        await group.save()
        
        # Log the action
        logger.info(
            "Updated group",
            extra={
                "group_id": str(id),
                "name": group.name,
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
        """
        Add a single account to a group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Check if account already in group
        if account_id in group.accounts:
            raise ValidationError(
                "Account already in group",
                context={"account_id": account_id, "group_id": str(group_id)}
            )
        
        # Validate account reference
        await self._validate_account_reference(account_id)
        
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
        
        # Log the action
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
        """
        Remove a single account from a group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Check if account in group
        if account_id not in group.accounts:
            raise ValidationError(
                "Account not in group",
                context={"account_id": account_id, "group_id": str(group_id)}
            )
        
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
        
        # Log the action
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
        """
        Add multiple accounts to a group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Validate account references
        await self.validate_account_references(account_ids)
        
        # Filter accounts not already in group
        new_accounts = [acc_id for acc_id in account_ids if acc_id not in group.accounts]
        
        if not new_accounts:
            return group
        
        # Add accounts to group
        group.accounts.extend(new_accounts)
        group.modified_at = datetime.utcnow()
        await group.save()
        
        # Add references
        for account_id in new_accounts:
            await reference_manager.add_reference(
                source_type="Group",
                target_type="Account",
                source_id=str(group_id),
                target_id=account_id
            )
        
        # Log the action
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
        """
        Remove multiple accounts from a group.
        """
        # Get group
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
        for account_id in accounts_to_remove:
            await reference_manager.remove_reference(
                source_type="Group",
                target_type="Account",
                source_id=str(group_id),
                target_id=account_id
            )
        
        # Log the action
        logger.info(
            "Removed accounts from group",
            extra={"group_id": str(group_id), "account_count": len(accounts_to_remove)}
        )
        
        return group

    @handle_db_error("Failed to get group accounts", lambda self, group_id: {"group_id": str(group_id)})
    async def get_group_accounts(
        self,
        group_id: PydanticObjectId
    ) -> List[Dict[str, Any]]:
        """
        Get all accounts in a group with detailed information.
        """
        # Get group
        group = await self.get(group_id)
        
        # Return empty list if no accounts
        if not group.accounts:
            return []
        
        # Get detailed account information
        accounts = await reference_manager.get_references_by_ids(
            reference_type="Account",
            reference_ids=group.accounts
        )
        
        return accounts

    @handle_db_error("Failed to sync group balances", lambda self, group_id: {"group_id": str(group_id)})
    async def sync_balances(
        self,
        group_id: PydanticObjectId
    ) -> Dict[str, Any]:
        """
        Synchronize account balances for all accounts in a group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Process each account
        results = []
        total_balance = Decimal("0")
        total_equity = Decimal("0")
        active_accounts = 0
        error_count = 0
        
        # Get trading service
        trading_service = await reference_manager.get_service("TradingService")
        
        # Process each account
        for account_id in group.accounts:
            try:
                # Get account balance
                balance_info = await trading_service.get_account_balance(account_id=account_id)
                
                # Update totals
                total_balance += Decimal(str(balance_info["balance"]))
                total_equity += Decimal(str(balance_info["equity"]))
                
                if balance_info.get("is_active", False):
                    active_accounts += 1
                
                # Add result
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
        
        # Update group information
        group.total_balance = float(total_balance)
        group.total_equity = float(total_equity)
        group.active_accounts = active_accounts
        group.last_sync = datetime.utcnow()
        
        # Update error tracking
        if error_count > 0:
            group.error_count += 1
            if len(group.error_timestamps) >= 10:
                group.error_timestamps.pop(0)  # Keep only last 10
            group.error_timestamps.append(datetime.utcnow())
            group.last_error = f"Failed to sync {error_count} accounts"
        else:
            group.error_count = 0
            group.error_timestamps = []
            group.last_error = None
        
        # Save group changes
        group.modified_at = datetime.utcnow()
        await group.save()
        
        return {
            "success": error_count == 0,
            "total_balance": float(total_balance),
            "total_equity": float(total_equity),
            "active_accounts": active_accounts,
            "error_count": error_count,
            "results": results
        }

    @handle_db_error("Failed to verify WebSocket health", lambda self, group_id: {"group_id": str(group_id)})
    async def verify_websocket_health(
        self,
        group_id: PydanticObjectId
    ) -> Dict[str, Any]:
        """
        Verify WebSocket connections for all accounts in the group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Check WebSocket status for each account
        results = {}
        active_connections = 0
        
        for account_id in group.accounts:
            try:
                ws_status = await ws_manager.get_connection_status(account_id)
                if ws_status.get("connected", False):
                    active_connections += 1
                results[account_id] = ws_status
            except Exception as e:
                results[account_id] = {
                    "connected": False,
                    "error": str(e)
                }
        
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

    @handle_db_error("Failed to get group performance", lambda self, group_id, start_date, end_date, period: {"group_id": str(group_id), "date_range": f"{start_date} to {end_date}", "period": period})
    async def get_performance(
        self,
        group_id: PydanticObjectId,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = "daily"
    ) -> List[Dict[str, Any]]:
        """
        Get performance metrics for a group over a date range.
        """
        # Get group
        group = await self.get(group_id)
        
        # Parse dates
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
        
        # Get performance data
        return await DailyPerformance.get_aggregated_performance(
            account_ids=group.accounts,
            start_date=start,
            end_date=end,
            period=period
        )

    @handle_db_error("Failed to get current metrics", lambda self, group_id: {"group_id": str(group_id)})
    async def get_current_metrics(
        self,
        group_id: PydanticObjectId
    ) -> Dict[str, Any]:
        """
        Get current metrics for a group.
        """
        # Get group
        group = await self.get(group_id)
        
        # Get performance service
        performance_service = await reference_manager.get_service("PerformanceService")
        
        # Get risk metrics
        risk_metrics = await performance_service.get_risk_metrics(
            account_ids=group.accounts,
            group_id=str(group_id)
        )
        
        # Get today's date
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Get today's performance
        today_performance = await DailyPerformance.get_aggregated_performance(
            account_ids=group.accounts,
            start_date=datetime.strptime(today, "%Y-%m-%d"),
            end_date=datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1),
            period="daily"
        )
        
        # Build response
        return {
            "balances": {
                "total_balance": group.total_balance,
                "total_equity": group.total_equity,
                "active_accounts": group.active_accounts,
                "last_sync": group.last_sync.isoformat() if group.last_sync else None
            },
            "risk": risk_metrics,
            "today": today_performance[0] if today_performance else {"no_data": True},
            "websocket": {
                "active_connections": group.ws_connections,
                "last_check": group.last_ws_check.isoformat() if group.last_ws_check else None
            }
        }

    @handle_db_error("Failed to delete group", lambda self, id: {"group_id": str(id)})
    async def delete(self, id: PydanticObjectId) -> bool:
        """
        Delete a group and clean up references.
        """
        # Get group
        group = await self.get(id)
        
        # Clean up references
        if group.accounts:
            for account_id in group.accounts:
                await reference_manager.remove_reference(
                    source_type="Group",
                    target_type="Account",
                    source_id=str(id),
                    target_id=account_id
                )
        
        # Delete the group
        await group.delete()
        
        # Log the action
        logger.info(
            "Deleted group",
            extra={"group_id": str(id), "name": group.name}
        )
        
        return True

    # ----- EXPORT METHODS -----

    @handle_db_error("Failed to export group data", lambda self, group_id, format: {"group_id": str(group_id), "format": format})
    async def export_group_data(
        self,
        group_id: PydanticObjectId,
        format: str = "csv",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Tuple[io.BytesIO, str]:
        """
        Generate a consolidated export file for all accounts in a group.
        
        Args:
            group_id: Group ID
            format: Export format ("csv" or "xlsx")
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            Tuple containing (file buffer, filename)
        """
        group = await self.get(group_id)
        
        # Parse date filters if provided
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
        
        # Get account data
        accounts_data = []
        for account_id in group.accounts:
            try:
                account = await reference_manager.get_reference(
                    reference_id=account_id,
                    reference_type="Account"
                )
                if account:
                    accounts_data.append(account)
            except Exception as e:
                logger.warning(
                    f"Error getting account data for export",
                    extra={"account_id": account_id, "error": str(e)}
                )
        
        # Create export file
        buffer = io.BytesIO()
        
        if format == "csv" or not EXCEL_SUPPORT:
            # Generate CSV file
            writer = csv.writer(buffer)
            
            # Write header
            writer.writerow([
                "Account ID", "Account Name", "Exchange", "Initial Balance",
                "Current Balance", "Current Equity", "Open Positions", "Last Sync"
            ])
            
            # Write account data
            for account in accounts_data:
                writer.writerow([
                    account.get("id"),
                    account.get("name"),
                    account.get("exchange"),
                    account.get("initial_balance"),
                    account.get("current_balance"),
                    account.get("current_equity"),
                    account.get("open_positions"),
                    account.get("last_sync")
                ])
            
            filename = f"{group.name}_accounts_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        else:
            # Generate Excel file
            workbook = xlsxwriter.Workbook(buffer)
            worksheet = workbook.add_worksheet("Accounts")
            
            # Write header
            header = [
                "Account ID", "Account Name", "Exchange", "Initial Balance",
                "Current Balance", "Current Equity", "Open Positions", "Last Sync"
            ]
            for col, header_text in enumerate(header):
                worksheet.write(0, col, header_text)
            
            # Write account data
            for row, account in enumerate(accounts_data, start=1):
                worksheet.write(row, 0, account.get("id"))
                worksheet.write(row, 1, account.get("name"))
                worksheet.write(row, 2, account.get("exchange"))
                worksheet.write(row, 3, account.get("initial_balance"))
                worksheet.write(row, 4, account.get("current_balance"))
                worksheet.write(row, 5, account.get("current_equity"))
                worksheet.write(row, 6, account.get("open_positions"))
                worksheet.write(row, 7, account.get("last_sync"))
            
            workbook.close()
            filename = f"{group.name}_accounts_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
        
        # Reset buffer position
        buffer.seek(0)
        
        return buffer, filename

    @handle_db_error("Failed to export group trades", lambda self, group_id, format: {"group_id": str(group_id), "format": format})
    async def export_group_trades(
        self,
        group_id: PydanticObjectId,
        format: str = "csv",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Tuple[io.BytesIO, str]:
        """
        Export trade history for all accounts in a group.
        
        Args:
            group_id: Group ID
            format: Export format ("csv" or "xlsx")
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            Tuple containing (file buffer, filename)
        """
        group = await self.get(group_id)
        
        # Parse date filters if provided
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
        
        # Get trades for all accounts in the group
        all_trades = []
        for account_id in group.accounts:
            try:
                account_trades = await trade_crud.get_account_trades(
                    account_id=account_id,
                    start_date=start,
                    end_date=end
                )
                all_trades.extend(account_trades)
            except Exception as e:
                logger.warning(
                    f"Error getting account trades for export",
                    extra={"account_id": account_id, "error": str(e)}
                )
        
        # Create export file
        buffer = io.BytesIO()
        
        if format == "csv" or not EXCEL_SUPPORT:
            # Generate CSV file
            writer = csv.writer(buffer)
            
            # Write header
            writer.writerow([
                "Account ID", "Symbol", "Side", "Size", "Entry Price", "Exit Price",
                "P&L", "P&L %", "Executed At", "Closed At", "Duration"
            ])
            
            # Write trade data
            for trade in all_trades:
                writer.writerow([
                    trade.get("account_id"),
                    trade.get("symbol"),
                    trade.get("side"),
                    trade.get("size"),
                    trade.get("entry_price"),
                    trade.get("exit_price"),
                    trade.get("pnl"),
                    trade.get("pnl_percentage"),
                    trade.get("executed_at"),
                    trade.get("closed_at"),
                    trade.get("duration")
                ])
            
            filename = f"{group.name}_trades_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        else:
            # Generate Excel file
            workbook = xlsxwriter.Workbook(buffer)
            worksheet = workbook.add_worksheet("Trades")
            
            # Write header
            header = [
                "Account ID", "Symbol", "Side", "Size", "Entry Price", "Exit Price",
                "P&L", "P&L %", "Executed At", "Closed At", "Duration"
            ]
            for col, header_text in enumerate(header):
                worksheet.write(0, col, header_text)
            
            # Write trade data
            for row, trade in enumerate(all_trades, start=1):
                worksheet.write(row, 0, trade.get("account_id"))
                worksheet.write(row, 1, trade.get("symbol"))
                worksheet.write(row, 2, trade.get("side"))
                worksheet.write(row, 3, trade.get("size"))
                worksheet.write(row, 4, trade.get("entry_price"))
                worksheet.write(row, 5, trade.get("exit_price"))
                worksheet.write(row, 6, trade.get("pnl"))
                worksheet.write(row, 7, trade.get("pnl_percentage"))
                worksheet.write(row, 8, trade.get("executed_at"))
                worksheet.write(row, 9, trade.get("closed_at"))
                worksheet.write(row, 10, trade.get("duration"))
            
            workbook.close()
            filename = f"{group.name}_trades_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
        
        # Reset buffer position
        buffer.seek(0)
        
        return buffer, filename

    @handle_db_error("Failed to export group performance", lambda self, group_id, period, format: {"group_id": str(group_id), "period": period, "format": format})
    async def export_group_performance(
        self,
        group_id: PydanticObjectId,
        period: str = "daily",
        format: str = "csv",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Tuple[io.BytesIO, str]:
        """
        Export performance metrics for a group.
        
        Args:
            group_id: Group ID
            period: Performance period ("daily", "weekly", "monthly")
            format: Export format ("csv" or "xlsx")
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            Tuple containing (file buffer, filename)
        """
        group = await self.get(group_id)
        
        # Parse date filters if provided
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.utcnow()
        
        # Get performance data
        performance_data = await DailyPerformance.get_aggregated_performance(
            account_ids=group.accounts,
            start_date=start,
            end_date=end,
            period=period
        )
        
        # Create export file
        buffer = io.BytesIO()
        
        if format == "csv" or not EXCEL_SUPPORT:
            # Generate CSV file
            writer = csv.writer(buffer)
            
            # Write header
            writer.writerow([
                "Date", "Starting Balance", "Closing Balance", "Starting Equity", "Closing Equity",
                "Trades", "Winning Trades", "PnL", "Win Rate", "ROI"
            ])
            
            # Write performance data
            for perf in performance_data:
                writer.writerow([
                    perf.get("date"),
                    perf.get("starting_balance"),
                    perf.get("closing_balance"),
                    perf.get("starting_equity"),
                    perf.get("closing_equity"),
                    perf.get("trades"),
                    perf.get("winning_trades"),
                    perf.get("pnl"),
                    perf.get("win_rate"),
                    perf.get("roi")
                ])
            
            filename = f"{group.name}_performance_{period}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        else:
            # Generate Excel file
            workbook = xlsxwriter.Workbook(buffer)
            worksheet = workbook.add_worksheet("Performance")
            
            # Write header
            header = [
                "Date", "Starting Balance", "Closing Balance", "Starting Equity", "Closing Equity",
                "Trades", "Winning Trades", "PnL", "Win Rate", "ROI"
            ]
            for col, header_text in enumerate(header):
                worksheet.write(0, col, header_text)
            
            # Write performance data
            for row, perf in enumerate(performance_data, start=1):
                worksheet.write(row, 0, perf.get("date"))
                worksheet.write(row, 1, perf.get("starting_balance"))
                worksheet.write(row, 2, perf.get("closing_balance"))
                worksheet.write(row, 3, perf.get("starting_equity"))
                worksheet.write(row, 4, perf.get("closing_equity"))
                worksheet.write(row, 5, perf.get("trades"))
                worksheet.write(row, 6, perf.get("winning_trades"))
                worksheet.write(row, 7, perf.get("pnl"))
                worksheet.write(row, 8, perf.get("win_rate"))
                worksheet.write(row, 9, perf.get("roi"))
            
            workbook.close()
            filename = f"{group.name}_performance_{period}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
        
        # Reset buffer position
        buffer.seek(0)
        
        return buffer, filename


# Import dependencies at the bottom to avoid circular imports
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.crud.crud_trade import trade as trade_crud

# Create a singleton instance
group = CRUDGroup(AccountGroup)