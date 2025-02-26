"""
Group model with enhanced service integration and error handling.

Features:
- Multi-group management
- Enhanced error handling and recovery
- WebSocket integration
- Performance tracking
- Reference validation
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Optional, Any

from beanie import Document, before_event, Replace, Insert, Indexed, PydanticObjectId
from pydantic import Field, field_validator

from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.logging.logger import get_logger

# Import external services at the top for consistency
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager
from app.services.telegram.service import telegram_bot

logger = get_logger(__name__)

def lazy_handle_db_error(message: str, context_func: callable):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # Import locally to avoid circular dependency
                from app.crud.decorators import handle_db_error
                await handle_db_error(
                    error=e,
                    context=context_func(*args, **kwargs),
                    log_message=message,
                )
                raise DatabaseError(message, context=context_func(*args, **kwargs)) from e
        return wrapper
    return decorator

class AccountGroup(Document):
    """
    Represents a grouping of trading accounts with enhanced tracking.
    
    Features:
    - Account management
    - Performance monitoring 
    - WebSocket status tracking
    - Enhanced error recovery
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

    # Account references
    accounts: List[str] = Field(
        default_factory=list,
        description="API account IDs"  
    )

    # Performance limits
    max_drawdown: float = Field(
        25.0,
        description="Maximum allowed drawdown percentage",
        gt=0,
        le=100
    )
    target_monthly_roi: float = Field(
        5.0,
        description="Target monthly ROI percentage",
        gt=0
    )
    risk_limit: float = Field(
        5.0,
        description="Maximum risk per trade percentage",
        gt=0,
        le=100
    )

    # Capacity limits
    max_accounts: int = Field(
        10,
        description="Maximum allowed accounts in group",
        gt=0
    )
    max_allocation: float = Field(
        100000.0,
        description="Maximum total allocation across accounts",
        gt=0
    )
    min_account_balance: float = Field(
        100.0,
        description="Minimum required account balance",
        gt=0  
    )

    # Quick access metrics
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
        description="Number of assigned accounts",
        ge=0
    )

    # WebSocket tracking 
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

    async def validate_balance_constraints(self) -> None:
        try:
            total_allocation = Decimal("0")
            for account_id in self.accounts:
                account = await reference_manager.get_reference(account_id)
                if not account:
                    continue
                balance = Decimal(str(account.get("current_balance", 0)))
                if balance < self.min_account_balance:
                    raise ValidationError(
                        "Account below minimum balance",
                        context={
                            "account_id": account_id,
                            "balance": str(balance),
                            "minimum": str(self.min_account_balance)
                        }
                    )
                total_allocation += Decimal(str(account.get("current_equity", 0)))
            if total_allocation > Decimal(str(self.max_allocation)):
                raise ValidationError(
                    "Maximum allocation exceeded",
                    context={
                        "current": str(total_allocation),
                        "max": str(self.max_allocation)
                    }
                )
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Balance constraint validation failed",
                context={"group_id": str(self.id), "error": str(e)}
            )

    @before_event([Replace, Insert])
    async def validate_references(self):
        try:
            if len(self.accounts) > self.max_accounts:
                raise ValidationError(
                    "Maximum account limit exceeded",
                    context={"current": len(self.accounts), "max": self.max_accounts}
                )
            await self.validate_balance_constraints()
            seen_accounts = set()
            for account_id in self.accounts:
                if account_id in seen_accounts:
                    raise ValidationError(
                        "Duplicate account reference",
                        context={"account_id": account_id}
                    )
                seen_accounts.add(account_id)
                if not await reference_manager.validate_reference(
                    source_type="Group",
                    target_type="Account", 
                    reference_id=account_id
                ):
                    raise ValidationError(
                        "Invalid account reference",
                        context={"account_id": account_id}
                    )
            active_count = 0
            ws_connections = 0
            for account_id in self.accounts:
                status = await self.validate_account_status(account_id)
                if status.get("is_active", False):
                    active_count += 1
                if status.get("ws_connected", False):
                    ws_connections += 1
            self.active_accounts = active_count
            self.ws_connections = ws_connections
            now = datetime.utcnow()
            self.last_ws_check = now
            self.modified_at = now
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "group_id": str(self.id),
                    "accounts": len(self.accounts),
                    "error": str(e)
                }
            )

    async def validate_account_status(self, account_id: str) -> Dict[str, bool]:
        try:
            account = await reference_manager.get_reference(account_id)
            if not account:
                raise ValidationError(
                    "Account not found",
                    context={"account_id": account_id}
                )
            trading_service = await reference_manager.get_service("TradingService")
            status_check = await trading_service.verify_account_status(account_id)
            ws_status = False
            if status_check.get("is_active", False):
                try:
                    ws_info = await ws_manager.get_connection_status(account_id)
                    ws_status = ws_info.get("connected", False)
                except Exception as e:
                    logger.warning(
                        f"Failed to get WebSocket status for {account_id}",
                        extra={"error": str(e)}
                    )
            return {
                "is_active": status_check.get("is_active", False),
                "has_balance": status_check.get("has_balance", False),
                "ws_connected": ws_status,
                "can_trade": all([
                    status_check.get("is_active", False),
                    status_check.get("has_balance", False),
                    ws_status
                ])
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Account status validation failed",
                context={"account_id": account_id, "error": str(e)}
            )

    async def add_account(self, account_id: str) -> None:
        try:
            if account_id in self.accounts:
                raise ValidationError(
                    "Account already in group",
                    context={"account_id": account_id, "group_id": str(self.id)}
                )
            if len(self.accounts) >= self.max_accounts:
                raise ValidationError(
                    "Group at maximum capacity",
                    context={"current": len(self.accounts), "max": self.max_accounts}
                )
            if not await reference_manager.validate_reference(
                source_type="Group",
                target_type="Account",
                reference_id=account_id
            ):
                raise ValidationError(
                    "Invalid account reference",
                    context={"account_id": account_id}
                )
            self.accounts.append(account_id)
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info(
                "Added account to group",
                extra={"group_id": str(self.id), "account_id": account_id}
            )
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to add account",
                context={"group_id": str(self.id), "account_id": account_id, "error": str(e)}
            )

    async def remove_account(self, account_id: str) -> None:
        try:
            if account_id not in self.accounts:
                raise ValidationError(
                    "Account not in group",
                    context={"account_id": account_id, "group_id": str(self.id)}
                )
            self.accounts.remove(account_id)
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info(
                "Removed account from group",
                extra={"group_id": str(self.id), "account_id": account_id}
            )
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to remove account",
                context={"group_id": str(self.id), "account_id": account_id, "error": str(e)}
            )

    async def sync_balances(self) -> Dict[str, Any]:
        try:
            results = []
            total_balance = Decimal("0")
            total_equity = Decimal("0")
            active_count = 0
            error_count = 0
            trading_service = await reference_manager.get_service("TradingService")
            for account_id in self.accounts:
                try:
                    balance_info = await trading_service.get_account_balance(account_id=account_id)
                    total_balance += Decimal(str(balance_info["balance"]))
                    total_equity += Decimal(str(balance_info["equity"]))
                    if balance_info.get("is_active", False):
                        active_count += 1
                    results.append({
                        "account_id": account_id,
                        "success": True,
                        "balance": float(balance_info["balance"]),
                        "equity": float(balance_info["equity"])
                    })
                except Exception as inner_e:
                    error_count += 1
                    results.append({
                        "account_id": account_id,
                        "success": False,
                        "error": str(inner_e)
                    })
            if error_count > 0:
                self.error_count += 1
                self.error_timestamps.append(datetime.utcnow())
                self.error_timestamps = self.error_timestamps[-10:]
            else:
                self.error_count = 0
                self.error_timestamps = []
            now = datetime.utcnow()
            self.total_balance = float(total_balance)
            self.total_equity = float(total_equity)
            self.active_accounts = active_count
            self.last_sync = now
            self.modified_at = now
            await self.save()
            return {
                "success": error_count == 0,
                "total_balance": float(total_balance),
                "total_equity": float(total_equity),
                "active_accounts": active_count,
                "error_count": error_count,
                "results": results
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to sync balances",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def get_daily_performance(self) -> Dict[str, Any]:
        try:
            performance_service = await reference_manager.get_service("PerformanceService")
            metrics = await performance_service.get_group_metrics(
                account_ids=self.accounts,
                date=datetime.utcnow()
            )
            return {
                **metrics,
                "group_id": str(self.id),
                "name": self.name,
                "active_accounts": self.active_accounts,
                "total_balance": self.total_balance,
                "total_equity": self.total_equity
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to get daily performance",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def get_historical_performance(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        try:
            performance_service = await reference_manager.get_service("PerformanceService")
            metrics = await performance_service.get_historical_metrics(
                account_ids=self.accounts,
                group_id=str(self.id),
                start_date=start_date,
                end_date=end_date
            )
            return {
                **metrics,
                "group_id": str(self.id),
                "name": self.name,
                "period": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                }
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to get historical performance",
                context={
                    "group_id": str(self.id),
                    "period": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def verify_websocket_health(self) -> Dict[str, Any]:
        try:
            results = {}
            active_connections = 0
            for account_id in self.accounts:
                try:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if ws_status.get("connected", False):
                        active_connections += 1
                    results[account_id] = ws_status
                except Exception as inner_e:
                    results[account_id] = {"connected": False, "error": str(inner_e)}
            self.ws_connections = active_connections
            now = datetime.utcnow()
            self.last_ws_check = now
            await self.save()
            return {
                "active_connections": active_connections,
                "total_accounts": len(self.accounts),
                "results": results,
                "timestamp": now.isoformat()
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to verify WebSocket health",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def get_risk_metrics(self) -> Dict[str, float]:
        try:
            total_allocation = self.total_balance
            if total_allocation <= 0:
                return {"drawdown": 0.0, "allocation_used": 0.0, "account_usage": 0.0}
            allocation_pct = (total_allocation / self.max_allocation) * 100
            account_usage = (len(self.accounts) / self.max_accounts) * 100
            performance_service = await reference_manager.get_service("PerformanceService")
            metrics = await performance_service.get_risk_metrics(
                account_ids=self.accounts,
                group_id=str(self.id)
            )
            return {
                "drawdown": metrics.get("current_drawdown", 0.0),
                "allocation_used": allocation_pct,
                "account_usage": account_usage
            }
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to get risk metrics",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def update_allocation_settings(
        self,
        max_allocation: Optional[float] = None,
        max_accounts: Optional[int] = None,
        min_account_balance: Optional[float] = None,
        risk_limit: Optional[float] = None
    ) -> None:
        try:
            if max_allocation is not None:
                if max_allocation <= 0:
                    raise ValidationError(
                        "Maximum allocation must be positive",
                        context={"max_allocation": max_allocation}
                    )
                self.max_allocation = max_allocation
            if max_accounts is not None:
                if max_accounts <= 0:
                    raise ValidationError(
                        "Maximum accounts must be positive",
                        context={"max_accounts": max_accounts}
                    )
                if max_accounts < len(self.accounts):
                    raise ValidationError(
                        "Maximum accounts cannot be less than current accounts",
                        context={"max_accounts": max_accounts, "current_accounts": len(self.accounts)}
                    )
                self.max_accounts = max_accounts
            if min_account_balance is not None:
                if min_account_balance <= 0:
                    raise ValidationError(
                        "Minimum balance must be positive",
                        context={"min_balance": min_account_balance}
                    )
                self.min_account_balance = min_account_balance
            if risk_limit is not None:
                if not 0 < risk_limit <= 100:
                    raise ValidationError(
                        "Risk limit must be between 0 and 100",
                        context={"risk_limit": risk_limit, "valid_range": "0-100"}
                    )
                self.risk_limit = risk_limit
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info(
                "Updated allocation settings",
                extra={
                    "group_id": str(self.id),
                    "settings": {
                        "max_allocation": max_allocation,
                        "max_accounts": max_accounts,
                        "min_balance": min_account_balance,
                        "risk_limit": risk_limit
                    }
                }
            )
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to update allocation settings",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def update_settings(
        self,
        description: Optional[str] = None,
        max_drawdown: Optional[float] = None,
        target_monthly_roi: Optional[float] = None
    ) -> None:
        try:
            if description is not None:
                self.description = description.strip()
            if max_drawdown is not None:
                if not 0 < max_drawdown <= 100:
                    raise ValidationError(
                        "Maximum drawdown must be between 0 and 100",
                        context={"max_drawdown": max_drawdown, "valid_range": "0-100"}
                    )
                self.max_drawdown = max_drawdown
            if target_monthly_roi is not None:
                if target_monthly_roi <= 0:
                    raise ValidationError(
                        "Target ROI must be positive",
                        context={"target_roi": target_monthly_roi}
                    )
                self.target_monthly_roi = target_monthly_roi
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info(
                "Updated group settings",
                extra={
                    "group_id": str(self.id),
                    "settings": {
                        "description": description,
                        "max_drawdown": max_drawdown,
                        "target_roi": target_monthly_roi
                    }
                }
            )
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise DatabaseError(
                "Failed to update group settings",
                context={"group_id": str(self.id), "error": str(e)}
            )

    async def add_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> "AccountGroup":
        group = await self.get(group_id)
        await self.validate_accounts(account_ids)
        new_accounts = [acc_id for acc_id in account_ids if acc_id not in group.accounts]
        if new_accounts:
            group.accounts.extend(new_accounts)
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
            await group.save()
        logger.info(
            "Added accounts to group",
            extra={"group_id": str(group_id), "account_count": len(new_accounts)}
        )
        return group

    async def remove_accounts(
        self,
        group_id: PydanticObjectId,
        account_ids: List[str]
    ) -> "AccountGroup":
        group = await self.get(group_id)
        accounts_to_remove = [acc_id for acc_id in group.accounts if acc_id in account_ids]
        if accounts_to_remove:
            for acc_id in accounts_to_remove:
                group.accounts.remove(acc_id)
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
            await group.save()
        logger.info(
            "Removed accounts from group",
            extra={"group_id": str(group_id), "account_count": len(accounts_to_remove)}
        )
        return group

    async def get_performance(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        group = await self.get(group_id)
        performance = await DailyPerformance.get_aggregated_performance(
            account_ids=group.accounts,
            start_date=start_date,
            end_date=end_date
        )
        return performance

    async def get_period_stats(
        self,
        group_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        group = await self.get(group_id)
        stats = await DailyPerformance.get_period_statistics(
            account_ids=group.accounts,
            start_date=start_date,
            end_date=end_date
        )
        return stats

    async def verify_websocket_health(self) -> Dict[str, Any]:
        try:
            results = {}
            active_connections = 0
            for account_id in self.accounts:
                try:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if ws_status.get("connected", False):
                        active_connections += 1
                    results[account_id] = ws_status
                except Exception as inner_e:
                    results[account_id] = {"connected": False, "error": str(inner_e)}
            self.ws_connections = active_connections
            now = datetime.utcnow()
            self.last_ws_check = now
            await self.save()
            return {
                "active_connections": active_connections,
                "total_accounts": len(self.accounts),
                "results": results,
                "timestamp": now.isoformat()
            }
        except Exception as e:
            raise DatabaseError("Failed to get WebSocket health", context={"group_id": str(self.id), "error": str(e)})

    async def get_risk_metrics(self) -> Dict[str, float]:
        try:
            total_allocation = self.total_balance
            if total_allocation <= 0:
                return {"drawdown": 0.0, "allocation_used": 0.0, "account_usage": 0.0}
            allocation_pct = (total_allocation / self.max_allocation) * 100
            account_usage = (len(self.accounts) / self.max_accounts) * 100
            performance_service = await reference_manager.get_service("PerformanceService")
            metrics = await performance_service.get_risk_metrics(
                account_ids=self.accounts,
                group_id=str(self.id)
            )
            return {
                "drawdown": metrics.get("current_drawdown", 0.0),
                "allocation_used": allocation_pct,
                "account_usage": account_usage
            }
        except Exception as e:
            raise DatabaseError("Failed to get risk metrics", context={"group_id": str(self.id), "error": str(e)})

    async def update_allocation_settings(
        self,
        max_allocation: Optional[float] = None,
        max_accounts: Optional[int] = None,
        min_account_balance: Optional[float] = None,
        risk_limit: Optional[float] = None
    ) -> None:
        try:
            if max_allocation is not None:
                if max_allocation <= 0:
                    raise ValidationError("Maximum allocation must be positive", context={"max_allocation": max_allocation})
                self.max_allocation = max_allocation
            if max_accounts is not None:
                if max_accounts <= 0:
                    raise ValidationError("Maximum accounts must be positive", context={"max_accounts": max_accounts})
                if max_accounts < len(self.accounts):
                    raise ValidationError("Maximum accounts cannot be less than current accounts", context={"max_accounts": max_accounts, "current_accounts": len(self.accounts)})
                self.max_accounts = max_accounts
            if min_account_balance is not None:
                if min_account_balance <= 0:
                    raise ValidationError("Minimum balance must be positive", context={"min_balance": min_account_balance})
                self.min_account_balance = min_account_balance
            if risk_limit is not None:
                if not 0 < risk_limit <= 100:
                    raise ValidationError("Risk limit must be between 0 and 100", context={"risk_limit": risk_limit, "valid_range": "0-100"})
                self.risk_limit = risk_limit
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info("Updated allocation settings", extra={"group_id": str(self.id), "settings": {"max_allocation": max_allocation, "max_accounts": max_accounts, "min_balance": min_account_balance, "risk_limit": risk_limit}})
        except Exception as e:
            raise DatabaseError("Failed to update allocation settings", context={"group_id": str(self.id), "error": str(e)})

    async def update_settings(
        self,
        description: Optional[str] = None,
        max_drawdown: Optional[float] = None,
        target_monthly_roi: Optional[float] = None
    ) -> None:
        try:
            if description is not None:
                self.description = description.strip()
            if max_drawdown is not None:
                if not 0 < max_drawdown <= 100:
                    raise ValidationError("Maximum drawdown must be between 0 and 100", context={"max_drawdown": max_drawdown, "valid_range": "0-100"})
                self.max_drawdown = max_drawdown
            if target_monthly_roi is not None:
                if target_monthly_roi <= 0:
                    raise ValidationError("Target ROI must be positive", context={"target_roi": target_monthly_roi})
                self.target_monthly_roi = target_monthly_roi
            self.modified_at = datetime.utcnow()
            await self.save()
            logger.info("Updated group settings", extra={"group_id": str(self.id), "settings": {"description": description, "max_drawdown": max_drawdown, "target_roi": target_monthly_roi}})
        except Exception as e:
            raise DatabaseError("Failed to update group settings", context={"group_id": str(self.id), "error": str(e)})

    async def add_to_group(self, group_id: str) -> None:
        try:
            if group_id in self.group_ids:
                raise ValidationError("Account already in group", context={"group_id": group_id, "account_id": str(self.id)})
            await self._validate_ref("Group", group_id)
            self.group_ids.append(group_id)
            self.touch()
            await self.save()
            await reference_manager.add_reference("Account", "Group", str(self.id), group_id)
            logger.info("Added account to group", extra={"account_id": str(self.id), "group_id": group_id})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to add to group", context={"account_id": str(self.id), "group_id": group_id, "error": str(e)})

    async def remove_from_group(self, group_id: str) -> None:
        try:
            if group_id not in self.group_ids:
                raise ValidationError("Account not in group", context={"group_id": group_id, "account_id": str(self.id)})
            self.group_ids.remove(group_id)
            self.touch()
            await self.save()
            await reference_manager.remove_reference("Account", "Group", str(self.id), group_id)
            logger.info("Removed account from group", extra={"account_id": str(self.id), "group_id": group_id})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to remove from group", context={"account_id": str(self.id), "group_id": group_id, "error": str(e)})

    @lazy_handle_db_error(
        "Failed to get position history",
        lambda self, start_date, end_date: {"account_id": str(self.id), "date_range": f"{start_date} to {end_date}"}
    )
    async def get_position_history(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[Dict[str, Any]]:
        try:
            return await DailyPerformance.get_account_performance(
                account_id=str(self.id),
                start_date=start_date,
                end_date=end_date
            )
        except Exception as e:
            raise DatabaseError("Failed to get position history", context={"account_id": str(self.id), "date_range": f"{start_date} to {end_date}", "error": str(e)})

    def to_dict(self) -> "ModelState":
        return {
            "account_info": {
                "id": str(self.id),
                "user_id": self.user_id,
                "name": self.name,
                "exchange": self.exchange.value,
                "is_testnet": self.is_testnet,
                "is_active": self.is_active,
            },
            "relationships": {
                "bot_id": self.bot_id,
                "group_ids": self.group_ids,
            },
            "balances": {
                "initial": str(self.initial_balance),
                "current": str(self.current_balance),
                "equity": str(self.current_equity),
            },
            "positions": {
                "open": self.open_positions,
                "total": self.total_positions,
                "successful": self.successful_positions,
                "value": str(self.position_value),
            },
            "fees": {
                "trading": str(self.trading_fees),
                "funding": str(self.funding_fees),
            },
            "settings": {
                "max_positions": self.max_positions,
                "max_drawdown": self.max_drawdown,
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error,
            },
        }

    def __repr__(self) -> str:
        return (
            f"Account(exchange={self.exchange}, balance={float(self.current_balance):.2f}, "
            f"positions={self.open_positions})"
        )

    def touch(self) -> None:
        self.modified_at = datetime.utcnow()
