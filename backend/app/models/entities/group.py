"""
Group model with enhanced service integration and error handling.

Features:
- Multi-group management
- Enhanced error handling and recovery
- WebSocket integration
- Performance tracking
- Reference validation
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional, Any
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

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
        description="Member account IDs"  
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
        1000.0,
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
        description="Number of active accounts",
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
        """Collection settings."""
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
        """
        Validate group name format.
        
        Args:
            v: Group name to validate
            
        Returns:
            str: Validated name
            
        Raises:
            ValidationError: If name format invalid
        """
        try:
            if not v or not v.strip():
                raise ValidationError(
                    "Group name cannot be empty",
                    context={"name": v}
                )

            name = v.strip()
            
            # Validate length
            if len(name) < 3:
                raise ValidationError(
                    "Group name too short",
                    context={
                        "name": name,
                        "min_length": 3
                    }
                )
                
            if len(name) > 32:
                raise ValidationError(
                    "Group name too long",
                    context={
                        "name": name,
                        "max_length": 32
                    }
                )
                
            # Validate characters
            if not all(c.isalnum() or c in '-_' for c in name):
                raise ValidationError(
                    "Group name contains invalid characters",
                    context={
                        "name": name,
                        "allowed": "alphanumeric, hyphen, underscore"
                    }
                )

            return name

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Group name validation failed",
                context={
                    "name": v,
                    "error": str(e)
                }
            )

    async def validate_balance_constraints(self) -> None:
        """
        Validate balance and allocation constraints.
        
        Raises:
            ValidationError: If constraints violated
        """
        try:
            total_allocation = Decimal('0')
            
            for account_id in self.accounts:
                account = await reference_manager.get_reference(account_id)
                if not account:
                    continue
                    
                balance = Decimal(str(account.get('current_balance', 0)))
                if balance < self.min_account_balance:
                    raise ValidationError(
                        "Account below minimum balance",
                        context={
                            "account_id": account_id,
                            "balance": str(balance),
                            "minimum": str(self.min_account_balance)
                        }
                    )
                    
                total_allocation += Decimal(str(account.get('current_equity', 0)))

            if total_allocation > Decimal(str(self.max_allocation)):
                raise ValidationError(
                    "Maximum allocation exceeded",
                    context={
                        "current": str(total_allocation),
                        "max": str(self.max_allocation)
                    }
                )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Balance constraint validation failed",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate model references with enhanced checking.
        
        Validates:
        - Account references exist and are active
        - No duplicate references
        - Group constraints not violated
        - WebSocket connections healthy
        
        Raises:
            ValidationError: If validation fails
            DatabaseError: If reference checks fail
        """
        try:
            # Check account limits
            if len(self.accounts) > self.max_accounts:
                raise ValidationError(
                    "Maximum account limit exceeded",
                    context={
                        "current": len(self.accounts),
                        "max": self.max_accounts
                    }
                )

            # Validate balance constraints
            await self.validate_balance_constraints()

            # Check for duplicates
            seen_accounts = set()
            for account_id in self.accounts:
                if account_id in seen_accounts:
                    raise ValidationError(
                        "Duplicate account reference",
                        context={"account_id": account_id}
                    )
                seen_accounts.add(account_id)

                # Validate reference
                if not await reference_manager.validate_reference(
                    source_type="Group",
                    target_type="Account", 
                    reference_id=account_id
                ):
                    raise ValidationError(
                        "Invalid account reference",
                        context={"account_id": account_id}
                    )

            # Check account statuses
            active_count = 0
            ws_connections = 0
            for account_id in self.accounts:
                status = await self.validate_account_status(account_id)
                if status["is_active"]:
                    active_count += 1
                if status["ws_connected"]:
                    ws_connections += 1

            # Update metrics
            self.active_accounts = active_count
            self.ws_connections = ws_connections
            self.last_ws_check = datetime.utcnow()
            self.modified_at = datetime.utcnow()

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "group_id": str(self.id),
                    "accounts": len(self.accounts),
                    "error": str(e)
                }
            )

    async def validate_account_status(self, account_id: str) -> Dict[str, bool]:
        """
        Validate account operational status.
        
        Args:
            account_id: Account to validate
            
        Returns:
            Dict: Status validation results
            
        Raises:
            ValidationError: If status check fails
        """
        try:
            account = await reference_manager.get_reference(account_id)
            if not account:
                raise ValidationError(
                    "Account not found",
                    context={"account_id": account_id}
                )
                
            # Get trading service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            # Check operational status
            status_check = await trading_service.verify_account_status(account_id)
            
            # Check WebSocket only if account active
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

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Account status validation failed",
                context={
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def add_account(self, account_id: str) -> None:
        """
        Add account to group with validation.
        
        Args:
            account_id: Account to add
            
        Raises:
            ValidationError: If account invalid
            DatabaseError: If addition fails
        """
        try:
            if account_id in self.accounts:
                raise ValidationError(
                    "Account already in group",
                    context={
                        "account_id": account_id,
                        "group_id": str(self.id)
                    }
                )

            # Check account limit
            if len(self.accounts) >= self.max_accounts:
                raise ValidationError(
                    "Group at maximum capacity",
                    context={
                        "current": len(self.accounts),
                        "max": self.max_accounts
                    }
                )

            # Validate account reference
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
                extra={
                    "group_id": str(self.id),
                    "account_id": account_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to add account",
                context={
                    "group_id": str(self.id),
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def remove_account(self, account_id: str) -> None:
        """
        Remove account from group.
        
        Args:
            account_id: Account to remove
            
        Raises:
            ValidationError: If not in group
            DatabaseError: If removal fails
        """
        try:
            if account_id not in self.accounts:
                raise ValidationError(
                    "Account not in group",
                    context={
                        "account_id": account_id,
                        "group_id": str(self.id)
                    }
                )

            self.accounts.remove(account_id)
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Removed account from group",
                extra={
                    "group_id": str(self.id),
                    "account_id": account_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to remove account",
                context={
                    "group_id": str(self.id),
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def sync_balances(self) -> Dict[str, Any]:
        """
        Sync balances with error handling.
        
        Returns:
            Dict with sync results
            
        Raises:
            DatabaseError: If sync fails
        """
        try:
            results = []
            total_balance = Decimal('0')
            total_equity = Decimal('0')
            active_count = 0
            error_count = 0

            # Get trading service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            # Sync each account
            for account_id in self.accounts:
                try:
                    # Get latest balance
                    balance_info = await trading_service.get_account_balance(
                        account_id=account_id
                    )

                    # Update metrics
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

                except Exception as e:
                    error_count += 1
                    results.append({
                        "account_id": account_id,
                        "success": False,
                        "error": str(e)
                    })

            # Update metrics
            if error_count > 0:
                self.error_count += 1
                self.error_timestamps.append(datetime.utcnow())
                # Keep last 10 errors only
                self.error_timestamps = self.error_timestamps[-10:]
            else:
                self.error_count = 0
                self.error_timestamps = []

            self.total_balance = float(total_balance)
            self.total_equity = float(total_equity)
            self.active_accounts = active_count
            self.last_sync = datetime.utcnow()
            self.modified_at = datetime.utcnow()
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
            raise DatabaseError(
                "Failed to sync balances",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    async def get_daily_performance(self) -> Dict[str, Any]:
        """
        Get daily performance metrics.
        
        Returns:
            Dict with performance metrics
            
        Raises:
            DatabaseError: If metrics retrieval fails
        """
        try:
            # Get performance service
            performance_service = await reference_manager.get_service(
                service_type="PerformanceService"
            )

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
            raise DatabaseError(
                "Failed to get daily performance",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    async def get_historical_performance(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get historical performance metrics.
        
        Args:
            start_date: Start of period
            end_date: End of period
            
        Returns:
            Dict: Historical performance metrics
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            # Get performance service
            performance_service = await reference_manager.get_service(
                service_type="PerformanceService"
            )

            metrics = await performance_service.get_historical_metrics(
                account_ids=self.accounts,
                group_id=str(self.id),
                start_date=start_date,
                end_date=end_date
            )

            # Add group context
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
            raise DatabaseError(
                "Failed to get historical performance",
                context={
                    "group_id": str(self.id),
                    "period": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def verify_websocket_health(self) -> Dict[str, Any]:
        """
        Verify WebSocket connections for all accounts.
        
        Returns:
            Dict with connection status
            
        Raises:
            WebSocketError: If verification fails
        """
        try:
            results = {}
            active_connections = 0

            for account_id in self.accounts:
                try:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if ws_status.get('connected', False):
                        active_connections += 1
                    results[account_id] = ws_status
                except Exception as e:
                    results[account_id] = {
                        "connected": False,
                        "error": str(e)
                    }

            self.ws_connections = active_connections
            self.last_ws_check = datetime.utcnow()
            await self.save()

            return {
                "active_connections": active_connections,
                "total_accounts": len(self.accounts),
                "results": results,
                "timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            raise WebSocketError(
                "WebSocket health verification failed",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    async def get_risk_metrics(self) -> Dict[str, float]:
        """
        Get group risk metrics.
        
        Returns:
            Dict with risk metrics
            
        Raises:
            DatabaseError: If calculation fails
        """
        try:
            total_allocation = self.total_balance
            if total_allocation <= 0:
                return {
                    "drawdown": 0.0,
                    "allocation_used": 0.0,
                    "account_usage": 0.0
                }

            # Calculate metrics
            allocation_pct = (total_allocation / self.max_allocation) * 100
            account_usage = (len(self.accounts) / self.max_accounts) * 100

            # Get drawdown from performance service
            performance_service = await reference_manager.get_service(
                service_type="PerformanceService"
            )
            
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
            raise DatabaseError(
                "Failed to get risk metrics",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    async def update_allocation_settings(
        self,
        max_allocation: Optional[float] = None,
        max_accounts: Optional[int] = None,
        min_account_balance: Optional[float] = None,
        risk_limit: Optional[float] = None
    ) -> None:
        """
        Update allocation and risk settings.
        
        Args:
            max_allocation: Maximum total allocation
            max_accounts: Maximum allowed accounts
            min_account_balance: Minimum account balance
            risk_limit: Maximum risk percentage
            
        Raises:
            ValidationError: If settings invalid
            DatabaseError: If update fails
        """
        try:
            # Validate and update max allocation
            if max_allocation is not None:
                if max_allocation <= 0:
                    raise ValidationError(
                        "Maximum allocation must be positive",
                        context={"max_allocation": max_allocation}
                    )
                self.max_allocation = max_allocation

            # Validate and update max accounts
            if max_accounts is not None:
                if max_accounts <= 0:
                    raise ValidationError(
                        "Maximum accounts must be positive",
                        context={"max_accounts": max_accounts}
                    )
                if max_accounts < len(self.accounts):
                    raise ValidationError(
                        "Maximum accounts cannot be less than current accounts",
                        context={
                            "max_accounts": max_accounts,
                            "current_accounts": len(self.accounts)
                        }
                    )
                self.max_accounts = max_accounts

            # Validate and update minimum balance
            if min_account_balance is not None:
                if min_account_balance <= 0:
                    raise ValidationError(
                        "Minimum balance must be positive",
                        context={"min_balance": min_account_balance}
                    )
                self.min_account_balance = min_account_balance

            # Validate and update risk limit
            if risk_limit is not None:
                if not 0 < risk_limit <= 100:
                    raise ValidationError(
                        "Risk limit must be between 0 and 100",
                        context={
                            "risk_limit": risk_limit,
                            "valid_range": "0-100"
                        }
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

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update allocation settings",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    async def update_settings(
        self,
        description: Optional[str] = None,
        max_drawdown: Optional[float] = None,
        target_monthly_roi: Optional[float] = None
    ) -> None:
        """
        Update group settings.
        
        Args:
            description: Group description
            max_drawdown: Maximum drawdown percentage
            target_monthly_roi: Target monthly ROI
            
        Raises:
            ValidationError: If settings invalid
            DatabaseError: If update fails
        """
        try:
            # Update description
            if description is not None:
                self.description = description.strip()

            # Validate and update max drawdown
            if max_drawdown is not None:
                if not 0 < max_drawdown <= 100:
                    raise ValidationError(
                        "Maximum drawdown must be between 0 and 100",
                        context={
                            "max_drawdown": max_drawdown,
                            "valid_range": "0-100"
                        }
                    )
                self.max_drawdown = max_drawdown

            # Validate and update target ROI
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

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update group settings",
                context={
                    "group_id": str(self.id),
                    "error": str(e)
                }
            )

    def to_dict(self) -> ModelState:
        """Convert to dictionary format."""
        return {
            "group_info": {
                "id": str(self.id),
                "name": self.name,
                "description": self.description
            },
            "accounts": {
                "ids": self.accounts,
                "total": len(self.accounts),
                "active": self.active_accounts
            },
            "settings": {
                "max_drawdown": self.max_drawdown,
                "target_monthly_roi": self.target_monthly_roi,
                "risk_limit": self.risk_limit,
                "max_accounts": self.max_accounts,
                "max_allocation": self.max_allocation,
                "min_account_balance": self.min_account_balance
            },
            "metrics": {
                "total_balance": self.total_balance,
                "total_equity": self.total_equity,
                "active_accounts": self.active_accounts,
                "ws_connections": self.ws_connections
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
                "last_ws_check": self.last_ws_check.isoformat() if self.last_ws_check else None
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error,
                "error_timestamps": [ts.isoformat() for ts in self.error_timestamps]
            }
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"AccountGroup(name='{self.name}', "
            f"accounts={len(self.accounts)}, "
            f"active={self.active_accounts}, "
            f"ws={self.ws_connections})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    ValidationError,
    DatabaseError,
    WebSocketError
)
from app.core.logging.logger import get_logger
from app.core.references import (
    ModelState,
    DateRange,
    ValidationResult
)
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)