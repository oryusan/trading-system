"""
Enhanced account CRUD operations with standardized error handling.

This module provides:
- Complete account lifecycle management
- Standardized error handling for all operations
- Service integrations for validation and synchronization
- Consistent business rule enforcement
"""

from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from decimal import Decimal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator

from .crud_base import CRUDBase
from app.models.entities.account import Account
from app.models.entities.bot import Bot 
from app.models.entities.daily_performance import DailyPerformance
from app.core.config.exchange_metadata import requires_passphrase
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, ExchangeError
from app.core.references import ExchangeType, TradeSource
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

# Service imports
from app.services.exchange.factory import exchange_factory
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.websocket.manager import ws_manager

logger = get_logger(__name__)

# ----------------------------
# Validation Schemas
# ----------------------------

class AccountCreate(BaseModel):
    """Schema for creating a new account."""
    user_id: str
    exchange: ExchangeType  
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    name: str
    initial_balance: Decimal
    is_testnet: bool = False
    bot_id: Optional[str] = None
    group_ids: List[str] = Field(default_factory=list)

    @field_validator("initial_balance")
    @classmethod
    def validate_initial_balance(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValidationError(
                "Initial balance must be positive",
                context={"initial_balance": str(v)}
            )
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValidationError(
                "Account name cannot be empty",
                context={"name": v}
            )
        return v.strip()

class AccountUpdate(BaseModel):
    """Schema for updating an account."""
    exchange: Optional[ExchangeType] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    passphrase: Optional[str] = None
    name: Optional[str] = None
    current_balance: Optional[Decimal] = None
    current_equity: Optional[Decimal] = None
    is_active: Optional[bool] = None
    bot_id: Optional[str] = None
    group_ids: Optional[List[str]] = None
    max_drawdown: Optional[float] = None

# ----------------------------
# CRUD Operations for Account
# ----------------------------

class CRUDAccount(CRUDBase[Account, AccountCreate, AccountUpdate]):
    """
    CRUD operations for the Account model with enhanced error handling.
    
    This class provides all operations for account management including:
    - Creation and validation
    - Balance updates and synchronization
    - Bot and group assignment
    - Performance tracking
    - Credential validation
    """

    @handle_db_error("Failed to retrieve account by API key", lambda self, api_key: {"api_key": f"{api_key[:8]}..."})
    async def get_by_api_key(self, api_key: str) -> Account:
        """Get an account by its API key."""
        account = await Account.find_one({"api_key": api_key})
        if not account:
            raise NotFoundError("Account not found", context={"api_key": f"{api_key[:8]}..."})
        return account

    @handle_db_error("Invalid exchange credentials", lambda self, exchange, api_key, api_secret, passphrase=None, is_testnet=False: {"exchange": exchange, "api_key": f"{api_key[:8]}...", "testnet": is_testnet})
    async def validate_exchange_credentials(
        self,
        exchange: ExchangeType,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        is_testnet: bool = False
    ) -> Dict[str, Any]:
        """
        Validate exchange credentials by attempting to connect and fetch balance.
        
        Args:
            exchange: Exchange type
            api_key: API key
            api_secret: API secret
            passphrase: Optional passphrase (required for some exchanges)
            is_testnet: Whether to use testnet
            
        Returns:
            Dict with balance information if successful
            
        Raises:
            ExchangeError: If credentials are invalid or connection fails
        """
        # Check exchange-specific requirements
        if requires_passphrase(exchange) and not passphrase:
            raise ValidationError(
                f"Passphrase is required for {exchange.value}",
                context={"exchange": exchange.value}
            )

        try:
            exchange_client = await exchange_factory.get_instance(
                exchange=exchange,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                testnet=is_testnet
            )
            balance = await exchange_client.get_balance()
            return balance
        except Exception as e:
            raise ExchangeError(
                "Exchange credential validation failed",
                context={
                    "exchange": exchange.value,
                    "error": str(e),
                    "testnet": is_testnet
                }
            ) from e

    @handle_db_error("Failed to validate bot assignment", lambda self, bot_id: {"bot_id": bot_id} if bot_id else {})
    async def validate_bot_assignment(self, bot_id: Optional[str]) -> None:
        """
        Validate that a bot reference exists and is valid.
        
        Args:
            bot_id: Optional bot ID to validate
            
        Raises:
            ValidationError: If bot reference is invalid
        """
        if bot_id:
            valid = await reference_manager.validate_reference(
                source_type="Account",
                target_type="Bot",
                reference_id=bot_id
            )
            if not valid:
                raise ValidationError("Invalid bot reference", context={"bot_id": bot_id})

    @handle_db_error("Failed to validate group assignments", lambda self, group_ids: {"group_ids": group_ids})
    async def validate_group_assignments(self, group_ids: List[str]) -> None:
        """
        Validate that all group references exist and are valid.
        
        Args:
            group_ids: List of group IDs to validate
            
        Raises:
            ValidationError: If any group reference is invalid or duplicate
        """
        seen_groups = set()
        for group_id in group_ids:
            if group_id in seen_groups:
                raise ValidationError("Duplicate group assignment", context={"group_id": group_id})
            seen_groups.add(group_id)
            valid = await reference_manager.validate_reference(
                source_type="Account",
                target_type="Group",
                reference_id=group_id
            )
            if not valid:
                raise ValidationError("Invalid group reference", context={"group_id": group_id})

    @handle_db_error("Failed to create account", lambda self, obj_in: {"user_id": obj_in.user_id, "exchange": obj_in.exchange})
    async def create(self, obj_in: AccountCreate) -> Account:
        """
        Create a new account with validation.
        
        Args:
            obj_in: Account creation data
            
        Returns:
            Created Account instance
            
        Raises:
            ValidationError: If validation fails
            ExchangeError: If exchange credential validation fails
            DatabaseError: If database operation fails
        """
        # Validate relationships
        await self.validate_bot_assignment(obj_in.bot_id)
        if obj_in.group_ids:
            await self.validate_group_assignments(obj_in.group_ids)
        
        # Validate exchange credentials and get initial balance
        balance_info = await self.validate_exchange_credentials(
            exchange=obj_in.exchange,
            api_key=obj_in.api_key,
            api_secret=obj_in.api_secret,
            passphrase=obj_in.passphrase,
            is_testnet=obj_in.is_testnet
        )
        
        # Create and save account
        db_obj = Account(
            user_id=obj_in.user_id,
            exchange=obj_in.exchange,
            api_key=obj_in.api_key,
            api_secret=obj_in.api_secret,
            passphrase=obj_in.passphrase,
            name=obj_in.name,
            initial_balance=obj_in.initial_balance,
            current_balance=balance_info.get("balance", obj_in.initial_balance),
            current_equity=balance_info.get("equity", obj_in.initial_balance),
            is_testnet=obj_in.is_testnet,
            bot_id=obj_in.bot_id,
            group_ids=obj_in.group_ids,
            created_at=datetime.utcnow(),
            last_sync=datetime.utcnow()
        )
        await db_obj.insert()
        
        # Set up related services if needed
        if obj_in.bot_id:
            try:
                # If part of a bot, set up WebSocket connection if bot is active
                bot = await Bot.get(PydanticObjectId(obj_in.bot_id))
                if bot and bot.status == "ACTIVE":
                    await ws_manager.create_connection(str(db_obj.id))
            except Exception as e:
                logger.warning(
                    "Failed to set up WebSocket for new account",
                    extra={"account_id": str(db_obj.id), "bot_id": obj_in.bot_id, "error": str(e)}
                )
        
        # Create performance record for new account
        try:
            await performance_service.initialize_account_performance(
                account_id=str(db_obj.id),
                initial_balance=float(obj_in.initial_balance)
            )
        except Exception as e:
            logger.warning(
                "Failed to initialize performance tracking",
                extra={"account_id": str(db_obj.id), "error": str(e)}
            )
        
        logger.info(
            "Created new account",
            extra={
                "account_id": str(db_obj.id),
                "user_id": obj_in.user_id,
                "exchange": obj_in.exchange.value
            }
        )
        return db_obj

    def _validate_positive(self, value: Decimal, field_name: str, account_id: str) -> None:
        """Validate that a decimal value is positive."""
        if value <= 0:
            raise ValidationError(
                f"{field_name.capitalize()} must be positive",
                context={field_name: str(value), "account_id": account_id}
            )

    @handle_db_error("Failed to update balance", lambda self, account_id, balance, equity: {"account_id": str(account_id), "balance": str(balance), "equity": str(equity)})
    async def update_balance(
        self,
        account_id: PydanticObjectId,
        balance: Decimal,
        equity: Decimal
    ) -> Account:
        """
        Update account balance and equity values.
        
        Args:
            account_id: Account ID
            balance: New balance value
            equity: New equity value
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If values are invalid
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        self._validate_positive(balance, "balance", str(account_id))
        self._validate_positive(equity, "equity", str(account_id))
        account.current_balance = balance
        account.current_equity = equity
        account.last_sync = datetime.utcnow()
        account.modified_at = datetime.utcnow()
        await account.save()
        
        # Update performance metrics
        await performance_service.update_daily_performance(
            account_id=str(account_id),
            date=datetime.utcnow(),
            metrics={"balance": float(balance), "equity": float(equity)}
        )
        
        logger.info(
            "Updated account balance",
            extra={"account_id": str(account_id), "balance": str(balance), "equity": str(equity)}
        )
        return account

    @handle_db_error("Failed to sync account", lambda self, account_id: {"account_id": str(account_id)})
    async def sync_balance(self, account_id: PydanticObjectId) -> Dict[str, Any]:
        """
        Synchronize account with exchange to update balance, positions, and other data.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dict with updated account information
            
        Raises:
            ExchangeError: If exchange communication fails
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        
        try:
            # Get exchange operations for this account
            exchange_client = await exchange_factory.get_instance(
                exchange=account.exchange,
                api_key=account.api_key,
                api_secret=account.api_secret,
                passphrase=account.passphrase,
                testnet=account.is_testnet
            )
            
            # Get balance and positions
            balance_info = await exchange_client.get_balance()
            positions = await exchange_client.get_all_positions()
            
            # Update account data
            account.current_balance = Decimal(str(balance_info["balance"]))
            account.current_equity = Decimal(str(balance_info["equity"]))
            account.position_value = sum(Decimal(str(p.get("notional_value", 0))) for p in positions)
            account.open_positions = len(positions)
            account.last_sync = datetime.utcnow()
            account.modified_at = datetime.utcnow()
            
            # Reset error counter on success
            if account.error_count > 0:
                account.error_count = 0
                account.last_error = None
            
            await account.save()
            
            # Update performance metrics
            await performance_service.update_daily_performance(
                account_id=str(account_id),
                date=datetime.utcnow(),
                metrics={
                    "balance": float(account.current_balance),
                    "equity": float(account.current_equity),
                    "open_positions": account.open_positions
                }
            )
            
            logger.info(
                "Synchronized account",
                extra={
                    "account_id": str(account_id),
                    "balance": str(account.current_balance),
                    "equity": str(account.current_equity),
                    "positions": account.open_positions
                }
            )
            
            return {
                "success": True,
                "balance": float(account.current_balance),
                "equity": float(account.current_equity),
                "positions": account.open_positions,
                "position_value": float(account.position_value)
            }
            
        except Exception as e:
            # Handle errors and track consecutive failures
            account.error_count += 1
            account.last_error = str(e)
            account.modified_at = datetime.utcnow()
            
            # Deactivate account after too many consecutive errors
            if account.error_count >= 5:
                account.is_active = False
                logger.warning(
                    "Account deactivated due to consecutive errors",
                    extra={"account_id": str(account_id), "error_count": account.error_count}
                )
            
            await account.save()
            
            raise ExchangeError(
                "Account synchronization failed",
                context={
                    "account_id": str(account_id),
                    "exchange": account.exchange.value,
                    "error_count": account.error_count,
                    "error": str(e)
                }
            )

    @handle_db_error("Failed to get performance data", lambda self, account_id, start_date, end_date: {"account_id": str(account_id), "date_range": f"{start_date} to {end_date}"})
    async def get_performance(
        self,
        account_id: PydanticObjectId,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """
        Get historical performance data for an account.
        
        Args:
            account_id: Account ID
            start_date: Start date for performance data
            end_date: End date for performance data
            
        Returns:
            List of performance data points
            
        Raises:
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Ensure account exists
        await self.get(account_id)
        
        # Get performance data from DailyPerformance model
        return await DailyPerformance.get_account_performance(
            account_id=str(account_id),
            start_date=start_date,
            end_date=end_date
        )

    @handle_db_error("Failed to validate account credentials", lambda self, account_id: {"account_id": str(account_id)})
    async def validate_credentials(self, account_id: PydanticObjectId) -> Dict[str, Any]:
        """
        Validate account API credentials with the exchange.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dict with validation results
            
        Raises:
            NotFoundError: If account not found
            ExchangeError: If validation fails
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        
        try:
            # Validate credentials with exchange
            result = await self.validate_exchange_credentials(
                exchange=account.exchange,
                api_key=account.api_key,
                api_secret=account.api_secret,
                passphrase=account.passphrase,
                is_testnet=account.is_testnet
            )
            
            # Update status if previously in error
            if account.error_count > 0:
                account.error_count = 0
                account.last_error = None
                account.is_active = True
                account.modified_at = datetime.utcnow()
                await account.save()
            
            logger.info(
                "Validated API credentials",
                extra={"account_id": str(account_id), "exchange": account.exchange.value}
            )
            
            return {"valid": True, "balance": result}
            
        except Exception as e:
            raise ExchangeError(
                "Credential validation failed",
                context={
                    "account_id": str(account_id),
                    "exchange": account.exchange.value,
                    "error": str(e)
                }
            )

    @handle_db_error("Failed to assign account to bot", lambda self, account_id, bot_id: {"account_id": str(account_id), "bot_id": bot_id})
    async def assign_to_bot(
        self,
        account_id: PydanticObjectId,
        bot_id: str
    ) -> Account:
        """
        Assign an account to a bot.
        
        Args:
            account_id: Account ID
            bot_id: Bot ID
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If bot reference is invalid
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Validate bot reference
        await self.validate_bot_assignment(bot_id)
        
        # Get and update account
        account = await self.get(account_id)
        
        # If already assigned to this bot, do nothing
        if account.bot_id == bot_id:
            return account
            
        # Update reference
        account.bot_id = bot_id
        account.modified_at = datetime.utcnow()
        await account.save()
        
        # Update reference manager
        await reference_manager.add_reference(
            source_type="Account",
            target_type="Bot",
            source_id=str(account_id),
            target_id=bot_id
        )
        
        # Set up WebSocket if bot is active
        try:
            bot = await Bot.get(PydanticObjectId(bot_id))
            if bot and bot.status == "ACTIVE":
                await ws_manager.create_connection(str(account_id))
        except Exception as e:
            logger.warning(
                "Failed to set up WebSocket after bot assignment",
                extra={"account_id": str(account_id), "bot_id": bot_id, "error": str(e)}
            )
        
        logger.info(
            "Assigned account to bot",
            extra={"account_id": str(account_id), "bot_id": bot_id}
        )
        return account

    @handle_db_error("Failed to unassign account from bot", lambda self, account_id: {"account_id": str(account_id)})
    async def unassign_from_bot(
        self,
        account_id: PydanticObjectId
    ) -> Account:
        """
        Remove bot assignment from an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Updated Account instance
            
        Raises:
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        
        # If not assigned to a bot, do nothing
        if not account.bot_id:
            return account
            
        # Store bot_id for reference removal
        old_bot_id = account.bot_id
        
        # Update account
        account.bot_id = None
        account.modified_at = datetime.utcnow()
        await account.save()
        
        # Update reference manager
        await reference_manager.remove_reference(
            source_type="Account",
            target_type="Bot",
            source_id=str(account_id),
            target_id=old_bot_id
        )
        
        # Clean up WebSocket connection
        try:
            await ws_manager.close_connection(str(account_id))
        except Exception as e:
            logger.warning(
                "Failed to close WebSocket after bot unassignment",
                extra={"account_id": str(account_id), "error": str(e)}
            )
        
        logger.info(
            "Unassigned account from bot",
            extra={"account_id": str(account_id), "previous_bot": old_bot_id}
        )
        return account

    @handle_db_error("Failed to assign account to groups", lambda self, account_id, group_ids: {"account_id": str(account_id), "group_ids": group_ids})
    async def assign_to_groups(
        self,
        account_id: PydanticObjectId,
        group_ids: List[str]
    ) -> Account:
        """
        Assign an account to multiple groups, replacing any existing group assignments.
        
        Args:
            account_id: Account ID
            group_ids: List of group IDs
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If group references are invalid
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Validate group assignments
        await self.validate_group_assignments(group_ids)
        
        # Get and update account
        account = await self.get(account_id)
        
        # Identify groups to add and remove
        old_groups = set(account.group_ids)
        new_groups = set(group_ids)
        
        # Groups to add
        for group_id in new_groups - old_groups:
            await reference_manager.add_reference(
                source_type="Account",
                target_type="Group",
                source_id=str(account_id),
                target_id=group_id
            )
            
        # Groups to remove
        for group_id in old_groups - new_groups:
            await reference_manager.remove_reference(
                source_type="Account",
                target_type="Group",
                source_id=str(account_id),
                target_id=group_id
            )
        
        # Update account
        account.group_ids = group_ids
        account.modified_at = datetime.utcnow()
        await account.save()
        
        logger.info(
            "Assigned account to groups",
            extra={
                "account_id": str(account_id),
                "group_count": len(group_ids),
                "groups_added": len(new_groups - old_groups),
                "groups_removed": len(old_groups - new_groups)
            }
        )
        return account

    @handle_db_error("Failed to add account to group", lambda self, account_id, group_id: {"account_id": str(account_id), "group_id": group_id})
    async def add_to_group(
        self,
        account_id: PydanticObjectId,
        group_id: str
    ) -> Account:
        """
        Add an account to a single group.
        
        Args:
            account_id: Account ID
            group_id: Group ID
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If group reference is invalid or already assigned
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Get account
        account = await self.get(account_id)
        
        # Check if already in group
        if group_id in account.group_ids:
            raise ValidationError(
                "Account already in group",
                context={"account_id": str(account_id), "group_id": group_id}
            )
        
        # Validate group reference
        valid = await reference_manager.validate_reference(
            source_type="Account",
            target_type="Group",
            reference_id=group_id
        )
        if not valid:
            raise ValidationError("Invalid group reference", context={"group_id": group_id})
        
        # Update account
        account.group_ids.append(group_id)
        account.modified_at = datetime.utcnow()
        await account.save()
        
        # Update reference
        await reference_manager.add_reference(
            source_type="Account",
            target_type="Group",
            source_id=str(account_id),
            target_id=group_id
        )
        
        logger.info(
            "Added account to group",
            extra={"account_id": str(account_id), "group_id": group_id}
        )
        return account

    @handle_db_error("Failed to remove account from group", lambda self, account_id, group_id: {"account_id": str(account_id), "group_id": group_id})
    async def remove_from_group(
        self,
        account_id: PydanticObjectId,
        group_id: str
    ) -> Account:
        """
        Remove an account from a single group.
        
        Args:
            account_id: Account ID
            group_id: Group ID
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If account not in group
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Get account
        account = await self.get(account_id)
        
        # Check if in group
        if group_id not in account.group_ids:
            raise ValidationError(
                "Account not in group",
                context={"account_id": str(account_id), "group_id": group_id}
            )
        
        # Update account
        account.group_ids.remove(group_id)
        account.modified_at = datetime.utcnow()
        await account.save()
        
        # Update reference
        await reference_manager.remove_reference(
            source_type="Account",
            target_type="Group",
            source_id=str(account_id),
            target_id=group_id
        )
        
        logger.info(
            "Removed account from group",
            extra={"account_id": str(account_id), "group_id": group_id}
        )
        return account

    @handle_db_error("Failed to check trade limits", lambda self, account_id: {"account_id": str(account_id)})
    async def check_trade_limits(
        self,
        account_id: PydanticObjectId
    ) -> Dict[str, bool]:
        """
        Check trading limits for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dict with trading limit checks
            
        Raises:
            NotFoundError: If account not found
            ValidationError: If check fails
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        
        try:
            # Calculate drawdown
            drawdown = 0.0
            if account.initial_balance > 0:
                drawdown = ((account.initial_balance - account.current_equity) / account.initial_balance) * 100
                drawdown = max(0.0, float(drawdown))
            
            # Define minimum balance requirement
            min_balance = Decimal("1000")
            
            # Check limits
            limits = {
                "is_active": account.is_active,
                "max_drawdown": drawdown < account.max_drawdown,
                "min_balance": account.current_balance >= min_balance,
                "error_threshold": account.error_count < 5
            }
            
            # Overall trading ability
            limits["can_trade"] = all(limits.values())
            
            # Log warning if trading restricted
            if not limits["can_trade"]:
                failed_checks = [k for k, passed in limits.items() if not passed and k != "can_trade"]
                logger.warning(
                    "Account trading restricted",
                    extra={
                        "account_id": str(account_id),
                        "failed_checks": failed_checks,
                        "drawdown": drawdown
                    }
                )
            
            return limits
            
        except Exception as e:
            raise ValidationError(
                "Trade limit check failed",
                context={"account_id": str(account_id), "error": str(e)}
            )

    @handle_db_error("Failed to record trade", lambda self, account_id, symbol, side, size, entry_price, source: {"account_id": str(account_id), "symbol": symbol, "side": side})
    async def record_trade(
        self,
        account_id: PydanticObjectId,
        symbol: str,
        side: str,
        size: Union[str, Decimal],
        entry_price: Union[str, Decimal],
        source: TradeSource
    ) -> None:
        """
        Record a trade for an account.
        
        Args:
            account_id: Account ID
            symbol: Trading symbol
            side: Trade side (buy/sell)
            size: Trade size
            entry_price: Entry price
            source: Trade source
            
        Raises:
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        account = await self.get(account_id)
        
        from app.models.entities.trade import Trade  # local import to avoid circular dependency
        
        # Convert string values to Decimal if needed
        size_decimal = Decimal(size) if isinstance(size, str) else size
        price_decimal = Decimal(entry_price) if isinstance(entry_price, str) else entry_price
        
        # Create trade record
        trade = Trade(
            account_id=str(account_id),
            exchange=account.exchange,
            symbol=symbol,
            side=side,
            size=size_decimal,
            entry_price=price_decimal,
            source=source,
            created_at=datetime.utcnow()
        )
        await trade.insert()
        
        # Update account trade count
        account.total_positions += 1
        account.modified_at = datetime.utcnow()
        await account.save()
        
        logger.info(
            "Recorded trade",
            extra={
                "account_id": str(account_id),
                "symbol": symbol,
                "side": side,
                "size": str(size_decimal)
            }
        )

    @handle_db_error("Failed to update account", lambda self, id, obj_in: {"account_id": str(id), "fields": list(obj_in.model_dump(exclude_unset=True).keys()) if hasattr(obj_in, "model_dump") else list(obj_in.keys())})
    async def update(
        self,
        id: PydanticObjectId,
        obj_in: Union[AccountUpdate, Dict[str, Any]]
    ) -> Account:
        """
        Update an account with additional validations.
        
        This method extends the base CRUD update method with additional validations
        for account-specific update operations.
        
        Args:
            id: Account ID
            obj_in: Update data
            
        Returns:
            Updated Account instance
            
        Raises:
            ValidationError: If validation fails
            NotFoundError: If account not found
            DatabaseError: If database operation fails
        """
        # Get current account
        account = await self.get(id)
        
        # Convert to dict if needed
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        
        # Validate credential updates if present
        if all(k in update_data for k in ["api_key", "api_secret"]):
            await self.validate_exchange_credentials(
                exchange=update_data.get("exchange", account.exchange),
                api_key=update_data["api_key"],
                api_secret=update_data["api_secret"],
                passphrase=update_data.get("passphrase", account.passphrase),
                is_testnet=account.is_testnet
            )
        
        # Validate bot assignment if present
        if "bot_id" in update_data and update_data["bot_id"] != account.bot_id:
            if update_data["bot_id"]:
                await self.validate_bot_assignment(update_data["bot_id"])
                
                # Handle reference updates
                await reference_manager.add_reference(
                    source_type="Account",
                    target_type="Bot",
                    source_id=str(id),
                    target_id=update_data["bot_id"]
                )
                
                # Remove old reference if exists
                if account.bot_id:
                    await reference_manager.remove_reference(
                        source_type="Account",
                        target_type="Bot",
                        source_id=str(id),
                        target_id=account.bot_id
                    )
            elif account.bot_id:
                # Remove bot reference if setting to None
                await reference_manager.remove_reference(
                    source_type="Account",
                    target_type="Bot",
                    source_id=str(id),
                    target_id=account.bot_id
                )
        
        # Validate group assignments if present
        if "group_ids" in update_data and update_data["group_ids"] is not None:
            await self.validate_group_assignments(update_data["group_ids"])
            
            # Handle reference updates
            old_groups = set(account.group_ids)
            new_groups = set(update_data["group_ids"])
            
            # Groups to add
            for group_id in new_groups - old_groups:
                await reference_manager.add_reference(
                    source_type="Account",
                    target_type="Group",
                    source_id=str(id),
                    target_id=group_id
                )
                
            # Groups to remove
            for group_id in old_groups - new_groups:
                await reference_manager.remove_reference(
                    source_type="Account",
                    target_type="Group",
                    source_id=str(id),
                    target_id=group_id
                )
        
        # Update account fields
        for field, value in update_data.items():
            setattr(account, field, value)
        
        # Update modified timestamp
        account.modified_at = datetime.utcnow()
        
        # Save changes
        await account.save()
        
        logger.info(
            "Updated account",
            extra={
                "account_id": str(id),
                "fields": list(update_data.keys())
            }
        )
        return account

    @handle_db_error("Failed to delete account", lambda self, id: {"account_id": str(id)})
    async def delete(self, id: PydanticObjectId) -> bool:
        """
        Delete an account with additional cleanup.
        
        This method extends the base CRUD delete method with additional cleanup
        operations specific to accounts.
        
        Args:
            id: Account ID
            
        Returns:
            True if successful
            
        Raises:
            NotFoundError: If account not found
            ValidationError: If account has open positions
            DatabaseError: If database operation fails
        """
        # Get account to be deleted
        account = await self.get(id)
        
        # Check if account has open positions
        if account.open_positions > 0:
            raise ValidationError(
                "Cannot delete account with open positions",
                context={"account_id": str(id), "open_positions": account.open_positions}
            )
        
        # Clean up references
        if account.bot_id:
            await reference_manager.remove_reference(
                source_type="Account",
                target_type="Bot",
                source_id=str(id),
                target_id=account.bot_id
            )
        
        for group_id in account.group_ids:
            await reference_manager.remove_reference(
                source_type="Account",
                target_type="Group",
                source_id=str(id),
                target_id=group_id
            )
        
        # Close WebSocket connections
        try:
            await ws_manager.close_connection(str(id))
        except Exception as e:
            logger.warning(
                "Failed to close WebSocket during account deletion",
                extra={"account_id": str(id), "error": str(e)}
            )
        
        # Delete performance data
        try:
            await performance_service.archive_account_data(str(id))
        except Exception as e:
            logger.warning(
                "Failed to archive performance data during account deletion",
                extra={"account_id": str(id), "error": str(e)}
            )
        
        # Delete account
        await account.delete()
        
        logger.info(
            "Deleted account",
            extra={"account_id": str(id), "exchange": account.exchange.value}
        )
        return True

# Create singleton instance for use throughout the application
account = CRUDAccount(Account)