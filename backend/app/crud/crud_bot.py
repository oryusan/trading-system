"""
Bot CRUD operations with centralized service integration.

This module serves as the primary integration point for all services related to bot management:
- Database operations (create, read, update, delete)
- Reference validation and management
- WebSocket connection management
- Exchange operations
- Notification services

All external service access is centralized here, keeping the entity model and API
endpoints focused on their respective concerns.
"""

from typing import List, Optional, Dict, Any, Union
import asyncio
from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, Field, field_validator, model_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.bot import Bot
from app.models.entities.account import Account
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, WebSocketError
from app.core.references import BotStatus, BotType, TimeFrame, TradeSource
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

# Import services for centralized integration
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.services.exchange.factory import exchange_factory
from app.services.telegram.service import telegram_bot
from app.core.config.constants import trading_constants

logger = get_logger(__name__)


class BotCreate(BaseModel):
    """Schema for creating a new bot."""
    name: str
    base_name: str
    timeframe: TimeFrame
    status: BotStatus = BotStatus.STOPPED
    connected_accounts: List[str] = []
    bot_type: BotType = BotType.AUTOMATED
    max_drawdown: float = Field(60.0, description="Maximum allowed drawdown percentage")
    risk_limit: float = Field(6.0, description="Maximum risk per trade percentage")
    max_allocation: float = Field(369000.0, description="Maximum total allocation allowed")
    min_account_balance: float = Field(100.0, description="Minimum required account balance")

    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValidationError("Bot name cannot be empty", context={"name": v})
        return v.strip()
    
    @model_validator(mode='after')
    def validate_name_format(self) -> 'BotCreate':
        """Validate that the name follows the expected format of base_name-timeframe for automated bots."""
        # Skip validation for manual bots
        if self.bot_type == BotType.MANUAL:
            return self
            
        # Validate name format for automated bots
        expected = f"{self.base_name}-{self.timeframe.value}"
        if self.name != expected:
            raise ValidationError(
                "Invalid bot name format", 
                context={
                    "name": self.name, 
                    "expected": expected, 
                    "base_name": self.base_name, 
                    "timeframe": self.timeframe.value
                }
            )
        return self


class BotManualCreate(BaseModel):
    """Schema for creating a new manual trading bot."""
    name: str = Field(..., description="Bot name")
    description: Optional[str] = Field(None, description="Optional description")
    connected_accounts: List[str] = Field(default_factory=list, description="Connected account IDs")
    status: BotStatus = Field(BotStatus.ACTIVE, description="Initial bot status")
    risk_limit: Optional[float] = Field(None, description="Maximum risk percentage per trade")
    max_allocation: Optional[float] = Field(None, description="Maximum total allocation")
    
    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValidationError("Bot name cannot be empty", context={"name": v})
        return v.strip()


class BotUpdate(BaseModel):
    """Schema for updating a bot."""
    name: Optional[str] = None
    base_name: Optional[str] = None
    timeframe: Optional[TimeFrame] = None
    status: Optional[BotStatus] = None
    connected_accounts: Optional[List[str]] = None
    max_drawdown: Optional[float] = None
    risk_limit: Optional[float] = None
    max_allocation: Optional[float] = None
    min_account_balance: Optional[float] = None


class CRUDBot(CRUDBase[Bot, BotCreate, BotUpdate]):
    """
    CRUD operations for the Bot model with centralized service integration.
    """

    @handle_db_error("Failed to retrieve bot by name", lambda self, name: {"name": name})
    async def get_by_name(self, name: str) -> Bot:
        """Get a bot by its name."""
        bot = await Bot.find_one({"name": name})
        if not bot:
            raise NotFoundError("Bot not found", context={"name": name})
        return bot

    @handle_db_error("Failed to validate bot name uniqueness", lambda self, name, exclude_id=None: {"name": name, "exclude_id": str(exclude_id) if exclude_id else None})
    async def validate_name_unique(
        self,
        name: str,
        exclude_id: Optional[PydanticObjectId] = None
    ) -> bool:
        """Check if a bot name is unique."""
        query = {"name": name}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        existing_bot = await Bot.find_one(query)
        return existing_bot is None

    @handle_db_error("Failed to validate accounts", lambda self, account_ids: {"account_ids": account_ids})
    async def validate_accounts(self, account_ids: List[str]) -> None:
        """
        Validate that the accounts exist and are available for connection.
        
        Args:
            account_ids: List of account IDs to validate
            
        Raises:
            ValidationError: If validation fails
        """
        # Check for duplicates
        if len(account_ids) != len(set(account_ids)):
            duplicates = list({acc for acc in account_ids if account_ids.count(acc) > 1})
            raise ValidationError("Duplicate account IDs found", context={"duplicates": duplicates})
        
        # Validate each account
        for account_id in account_ids:
            valid = await reference_manager.validate_reference(
                source_type="Bot",
                target_type="Account",
                reference_id=account_id
            )
            if not valid:
                raise ValidationError("Invalid account reference", context={"account_id": account_id})
            
            # Check if account is already assigned to another bot
            account = await Account.get(account_id)
            if account.bot_id and str(account.bot_id) != "":
                raise ValidationError(
                    "Account already connected to another bot",
                    context={"account_id": account_id, "bot_id": account.bot_id}
                )

    @handle_db_error("Failed to create bot", lambda self, obj_in: {"name": obj_in.name})
    async def create(self, obj_in: BotCreate) -> Bot:
        """
        Create a new bot with validation.
        
        Args:
            obj_in: Bot creation data
            
        Returns:
            The created bot
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Validate name uniqueness
        if not await self.validate_name_unique(obj_in.name):
            raise ValidationError("Bot name already exists", context={"name": obj_in.name})
        
        # Validate accounts if provided
        if obj_in.connected_accounts:
            await self.validate_accounts(obj_in.connected_accounts)
        
        # Create bot instance
        db_obj = Bot(
            name=obj_in.name,
            base_name=obj_in.base_name,
            timeframe=obj_in.timeframe,
            status=obj_in.status,
            bot_type=obj_in.bot_type,
            connected_accounts=obj_in.connected_accounts,
            max_drawdown=obj_in.max_drawdown,
            risk_limit=obj_in.risk_limit,
            max_allocation=obj_in.max_allocation,
            min_account_balance=obj_in.min_account_balance
        )
        
        # Save to database
        await db_obj.save()
        
        # Connect accounts if provided
        if obj_in.connected_accounts:
            await self.connect_accounts(db_obj.id, obj_in.connected_accounts)
        
        logger.info("Created new bot", extra={
            "bot_id": str(db_obj.id),
            "name": db_obj.name,
            "account_count": len(obj_in.connected_accounts),
            "bot_type": obj_in.bot_type.value
        })
        
        return db_obj

    @handle_db_error("Failed to create manual bot", lambda self, obj_in: {"name": obj_in.name})
    async def create_manual_bot(self, obj_in: BotManualCreate) -> Bot:
        """
        Create a new manual trading bot.
        
        Args:
            obj_in: Manual bot creation data
            
        Returns:
            The created manual bot
        """
        # Validate name uniqueness
        if not await self.validate_name_unique(obj_in.name):
            raise ValidationError("Bot name already exists", context={"name": obj_in.name})
        
        # Validate accounts if provided
        if obj_in.connected_accounts:
            await self.validate_accounts(obj_in.connected_accounts)
        
        # Create bot instance with manual type
        db_obj = Bot(
            name=obj_in.name,
            base_name=obj_in.name,  # For manual bots, base_name = name
            timeframe=TimeFrame.D1,  # Default timeframe for manual bots
            status=obj_in.status,
            bot_type=BotType.MANUAL,
            connected_accounts=obj_in.connected_accounts.copy(),
            risk_limit=obj_in.risk_limit or 6.0,  # Default risk limit
            max_allocation=obj_in.max_allocation or 369000.0,  # Default max allocation
            created_at=datetime.utcnow()
        )
        
        # Save to database
        await db_obj.save()
        
        # Connect accounts if provided
        if obj_in.connected_accounts:
            await self.connect_accounts(db_obj.id, obj_in.connected_accounts)
        
        logger.info("Created new manual bot", extra={
            "bot_id": str(db_obj.id),
            "name": db_obj.name,
            "account_count": len(obj_in.connected_accounts)
        })
        
        return db_obj

    @handle_db_error("Failed to update bot", lambda self, bot_id, updates: {"bot_id": str(bot_id), "fields": list(updates.keys())})
    async def update(self, bot_id: PydanticObjectId, obj_in: Union[BotUpdate, Dict[str, Any]]) -> Bot:
        """
        Update a bot with validation.
        
        Args:
            bot_id: ID of the bot to update
            obj_in: Update data
            
        Returns:
            The updated bot
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Get current bot
        db_obj = await self.get(bot_id)
        
        # Extract update data
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        
        # Handle special cases
        
        # 1. Status change
        if "status" in update_data and update_data["status"] != db_obj.status:
            new_status = update_data["status"]
            if not db_obj.is_valid_status_transition(new_status):
                raise ValidationError(
                    "Invalid status transition",
                    context={
                        "current": db_obj.status,
                        "attempted": new_status
                    }
                )
            
            # Handle status-specific WebSocket operations
            if new_status == BotStatus.ACTIVE:
                await self._activate_bot_connections(db_obj)
            elif new_status == BotStatus.STOPPED:
                await self._deactivate_bot_connections(db_obj)
        
        # 2. Account connections
        if "connected_accounts" in update_data and update_data["connected_accounts"] != db_obj.connected_accounts:
            # Validate new accounts
            if update_data["connected_accounts"]:
                await self.validate_accounts(update_data["connected_accounts"])
            
            # Determine accounts to add and remove
            old_accounts = set(db_obj.connected_accounts)
            new_accounts = set(update_data["connected_accounts"])
            accounts_to_add = new_accounts - old_accounts
            accounts_to_remove = old_accounts - new_accounts
            
            # Connect and disconnect accounts
            if accounts_to_add:
                await self._connect_accounts_internal(db_obj, list(accounts_to_add))
            
            if accounts_to_remove:
                await self._disconnect_accounts_internal(db_obj, list(accounts_to_remove))
        
        # Update general fields
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        
        # Touch modified timestamp
        db_obj.touch()
        
        # Save changes
        await db_obj.save()
        
        logger.info("Updated bot", extra={
            "bot_id": str(bot_id),
            "fields": list(update_data.keys())
        })
        
        return db_obj

    @handle_db_error("Failed to connect accounts", lambda self, bot_id, account_ids: {"bot_id": str(bot_id), "account_ids": account_ids})
    async def connect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        """
        Connect multiple accounts to a bot.
        
        Args:
            bot_id: ID of the bot
            account_ids: List of account IDs to connect
            
        Returns:
            The updated bot
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Validate accounts
        await self.validate_accounts(account_ids)
        
        # Connect accounts
        await self._connect_accounts_internal(bot, account_ids)
        
        # Save changes
        await bot.save()
        
        logger.info("Connected accounts to bot", extra={
            "bot_id": str(bot_id),
            "account_count": len(account_ids)
        })
        
        return bot

    async def _connect_accounts_internal(self, bot: Bot, account_ids: List[str]) -> None:
        """
        Internal helper for connecting accounts to a bot.
        
        Args:
            bot: Bot instance
            account_ids: List of account IDs to connect
        """
        # Update account references
        async def update_account(account_id: str):
            account = await Account.get(account_id)
            account.bot_id = str(bot.id)
            await account.save()
            
            # Set up WebSocket if bot is active
            if bot.status == BotStatus.ACTIVE:
                await ws_manager.create_connection(account_id)
                bot.update_subscription_status(account_id, True)
        
        # Process accounts concurrently
        await asyncio.gather(*(update_account(acc_id) for acc_id in account_ids))
        
        # Update bot's connected accounts
        for account_id in account_ids:
            if account_id not in bot.connected_accounts:
                bot.connected_accounts.append(account_id)

    @handle_db_error("Failed to disconnect accounts", lambda self, bot_id, account_ids: {"bot_id": str(bot_id), "account_ids": account_ids})
    async def disconnect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        """
        Disconnect multiple accounts from a bot.
        
        Args:
            bot_id: ID of the bot
            account_ids: List of account IDs to disconnect
            
        Returns:
            The updated bot
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Disconnect accounts
        await self._disconnect_accounts_internal(bot, account_ids)
        
        # Save changes
        await bot.save()
        
        logger.info("Disconnected accounts from bot", extra={
            "bot_id": str(bot_id),
            "account_count": len(account_ids)
        })
        
        return bot

    async def _disconnect_accounts_internal(self, bot: Bot, account_ids: List[str]) -> None:
        """
        Internal helper for disconnecting accounts from a bot.
        
        Args:
            bot: Bot instance
            account_ids: List of account IDs to disconnect
        """
        # Update account references
        async def disconnect_account(account_id: str):
            account = await Account.get(account_id)
            if account and account.bot_id == str(bot.id):
                account.bot_id = None
                await account.save()
                
                # Close WebSocket if needed
                if bot.status == BotStatus.ACTIVE:
                    await ws_manager.close_connection(account_id)
                    bot.update_subscription_status(account_id, False)
        
        # Process accounts concurrently
        await asyncio.gather(*(disconnect_account(acc_id) for acc_id in account_ids))
        
        # Update bot's connected accounts
        bot.connected_accounts = [acc_id for acc_id in bot.connected_accounts if acc_id not in account_ids]

    @handle_db_error("Failed to update bot status", lambda self, bot_id, status: {"bot_id": str(bot_id), "status": status})
    async def update_status(
        self,
        bot_id: PydanticObjectId,
        status: BotStatus
    ) -> Bot:
        """
        Update bot status with proper WebSocket connection management.
        
        Args:
            bot_id: ID of the bot
            status: New status
            
        Returns:
            The updated bot
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If database operation fails
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Validate status transition
        if not bot.is_valid_status_transition(status):
            raise ValidationError(
                "Invalid status transition",
                context={
                    "current": bot.status.value,
                    "attempted": status.value
                }
            )
        
        # Store previous status for logging
        previous_status = bot.status
        
        # Update status
        bot.status = status
        
        # Handle WebSocket connections based on status change
        if status == BotStatus.ACTIVE and previous_status != BotStatus.ACTIVE:
            await self._activate_bot_connections(bot)
        elif status == BotStatus.STOPPED and previous_status != BotStatus.STOPPED:
            await self._deactivate_bot_connections(bot)
        
        # Update timestamp
        bot.touch()
        
        # Save changes
        await bot.save()
        
        # Send notification
        await telegram_bot.notify_bot_status(str(bot_id), status)
        
        logger.info("Updated bot status", extra={
            "bot_id": str(bot_id),
            "previous": previous_status.value,
            "new": status.value
        })
        
        return bot

    async def _activate_bot_connections(self, bot: Bot) -> None:
        """
        Set up WebSocket connections for all accounts when a bot is activated.
        
        Args:
            bot: Bot instance
        """
        # Create WebSocket connections for each account
        for account_id in bot.connected_accounts:
            try:
                account = await reference_manager.get_reference(account_id, "Account")
                if not account:
                    logger.warning(
                        "Account not found during bot activation",
                        extra={"bot_id": str(bot.id), "account_id": account_id}
                    )
                    continue
                
                # Create WebSocket connection
                await ws_manager.create_connection(
                    identifier=account_id,
                    config={
                        "exchange": account["exchange"],
                        "api_key": account["api_key"],
                        "api_secret": account["api_secret"],
                        "passphrase": account.get("passphrase"),
                        "testnet": account.get("is_testnet", False)
                    }
                )
                
                # Subscribe to channels
                for channel in ["positions", "orders", "balances"]:
                    await ws_manager.subscribe(account_id, channel)
                
                # Update subscription status
                bot.update_subscription_status(account_id, True)
                
                logger.info(
                    "Activated WebSocket connection for bot account",
                    extra={"bot_id": str(bot.id), "account_id": account_id}
                )
            except Exception as e:
                logger.error(
                    "Failed to activate WebSocket connection",
                    extra={"bot_id": str(bot.id), "account_id": account_id, "error": str(e)}
                )

    async def _deactivate_bot_connections(self, bot: Bot) -> None:
        """
        Close WebSocket connections when a bot is stopped.
        
        Args:
            bot: Bot instance
        """
        # Close WebSocket connections for each subscribed account
        for account_id in list(bot.subscribed_accounts):  # Create a copy of the list since we're modifying it
            try:
                await ws_manager.close_connection(account_id)
                bot.update_subscription_status(account_id, False)
                
                logger.info(
                    "Deactivated WebSocket connection for bot account",
                    extra={"bot_id": str(bot.id), "account_id": account_id}
                )
            except Exception as e:
                logger.error(
                    "Failed to deactivate WebSocket connection",
                    extra={"bot_id": str(bot.id), "account_id": account_id, "error": str(e)}
                )

    @handle_db_error("Failed to process signal", lambda self, bot_id, signal_data: {"bot_id": str(bot_id), "signal": signal_data})
    async def process_signal(
        self,
        bot_id: PydanticObjectId,
        signal_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process a trading signal for a bot.
        
        Args:
            bot_id: ID of the bot
            signal_data: Signal data to process
            
        Returns:
            Results of signal processing
            
        Raises:
            ValidationError: If validation fails
            DatabaseError: If processing fails
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Validate bot status
        if bot.status != BotStatus.ACTIVE:
            raise ValidationError(
                "Bot not active",
                context={"bot_id": str(bot_id), "status": bot.status.value}
            )
        
        # Process signal for each connected account
        async def process_trade(account_id: str) -> Dict[str, Any]:
            try:
                operations = await exchange_factory.get_instance(account_id)
                trade_result = await operations.execute_trade(
                    account_id=account_id,
                    symbol=signal_data["symbol"],
                    side=signal_data["side"],
                    order_type=signal_data["order_type"],
                    risk_percentage=signal_data["risk_percentage"],
                    leverage=signal_data["leverage"],
                    take_profit=signal_data.get("take_profit"),
                    source=TradeSource.BOT
                )
                return {
                    "account_id": account_id,
                    "success": trade_result["success"],
                    "details": trade_result
                }
            except Exception as e:
                return {
                    "account_id": account_id,
                    "success": False,
                    "error": str(e)
                }
        
        # Execute trades in parallel
        results = await asyncio.gather(
            *(process_trade(account_id) for account_id in bot.connected_accounts)
        )
        
        # Process results
        success_count = sum(1 for r in results if r.get("success"))
        error_count = len(results) - success_count
        
        # Update bot metrics
        bot.record_signal_result(success_count, error_count)
        await bot.save()
        
        logger.info("Processed signal", extra={
            "bot_id": str(bot_id),
            "success_count": success_count,
            "error_count": error_count
        })
        
        return {
            "success": error_count == 0,
            "accounts_processed": len(results),
            "success_count": success_count,
            "error_count": error_count,
            "results": results
        }

    @handle_db_error("Failed to get bot performance", lambda self, bot_id, start_date, end_date: {"bot_id": str(bot_id), "date_range": f"{start_date} to {end_date}"})
    async def get_performance(
        self,
        bot_id: PydanticObjectId,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get performance metrics for a bot.
        
        Args:
            bot_id: ID of the bot
            start_date: Start date for metrics
            end_date: End date for metrics
            
        Returns:
            Performance metrics
            
        Raises:
            DatabaseError: If retrieval fails
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Get connected accounts
        if not bot.connected_accounts:
            return {
                "daily": [],
                "weekly": [],
                "monthly": [],
                "summary": {
                    "total_trades": 0,
                    "win_rate": 0,
                    "net_pnl": 0
                }
            }
        
        # Get daily performance
        daily_performance = await DailyPerformance.get_aggregated_performance(
            account_ids=bot.connected_accounts,
            start_date=start_date,
            end_date=end_date
        )
        
        # Get weekly performance
        weekly_performance = await DailyPerformance.get_aggregated_performance(
            account_ids=bot.connected_accounts,
            start_date=start_date,
            end_date=end_date,
            period="weekly"
        )
        
        # Get monthly performance
        monthly_performance = await DailyPerformance.get_aggregated_performance(
            account_ids=bot.connected_accounts,
            start_date=start_date,
            end_date=end_date,
            period="monthly"
        )
        
        # Get account-specific performance
        account_performance = {}
        for account_id in bot.connected_accounts:
            try:
                account = await reference_manager.get_reference(account_id, "Account")
                account_metrics = await DailyPerformance.get_account_performance(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date
                )
                account_performance[account_id] = {
                    "name": account.get("name", "Unknown"),
                    "metrics": account_metrics
                }
            except Exception as e:
                logger.warning(
                    "Failed to get account performance",
                    extra={"bot_id": str(bot_id), "account_id": account_id, "error": str(e)}
                )
        
        # Calculate summary statistics
        total_trades = sum(day.get("trades", 0) for day in daily_performance)
        winning_trades = sum(day.get("winning_trades", 0) for day in daily_performance)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        net_pnl = sum(day.get("pnl", 0) for day in daily_performance)
        
        return {
            "daily": daily_performance,
            "weekly": weekly_performance,
            "monthly": monthly_performance,
            "accounts": account_performance,
            "summary": {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "win_rate": win_rate,
                "net_pnl": net_pnl
            }
        }

    @handle_db_error("Failed to get active bots", lambda self, bot_type=None: {"bot_type": bot_type.value if bot_type and hasattr(bot_type, "value") else bot_type})
    async def get_active_bots(self, bot_type: Optional[BotType] = None) -> List[Bot]:
        """
        Get all active bots, optionally filtered by bot_type.
        
        Args:
            bot_type: Optional filter by bot type
            
        Returns:
            List of active bots
        """
        query = {"status": BotStatus.ACTIVE}
        if bot_type:
            query["bot_type"] = bot_type
        return await Bot.find(query).to_list()

    @handle_db_error("Failed to get bots by type", lambda self, bot_type: {"bot_type": bot_type.value if hasattr(bot_type, "value") else bot_type})
    async def get_bots_by_type(self, bot_type: BotType) -> List[Bot]:
        """
        Get all bots of a specific type.
        
        Args:
            bot_type: Type of bots to retrieve
            
        Returns:
            List of bots matching the specified type
        """
        return await Bot.find({"bot_type": bot_type}).to_list()

    @handle_db_error("Failed to get bots by timeframe", lambda self, timeframe: {"timeframe": timeframe})
    async def get_bots_by_timeframe(self, timeframe: TimeFrame) -> List[Bot]:
        """
        Get all bots for a specific timeframe.
        
        Args:
            timeframe: Timeframe to filter by
            
        Returns:
            List of bots
        """
        return await Bot.find({"timeframe": timeframe}).to_list()

    @handle_db_error("Failed to get connected accounts", lambda self, bot_id: {"bot_id": str(bot_id)})
    async def get_connected_accounts(self, bot_id: PydanticObjectId) -> List[Dict[str, Any]]:
        """
        Get detailed information about accounts connected to a bot.
        
        Args:
            bot_id: ID of the bot
            
        Returns:
            List of account details
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Get detailed account information
        accounts = []
        for account_id in bot.connected_accounts:
            try:
                account = await reference_manager.get_reference(account_id, "Account")
                if account:
                    # Get WebSocket status
                    try:
                        ws_status = await ws_manager.get_connection_status(account_id)
                    except Exception as e:
                        ws_status = {"connected": False, "error": str(e)}
                    
                    # Add to results
                    accounts.append({
                        **account,
                        "ws_status": ws_status
                    })
            except Exception as e:
                logger.warning(
                    "Failed to get account details",
                    extra={"bot_id": str(bot_id), "account_id": account_id, "error": str(e)}
                )
        
        return accounts

    @handle_db_error("Failed to verify trading readiness", lambda self, bot_id: {"bot_id": str(bot_id)})
    async def verify_trading_ready(self, bot_id: PydanticObjectId) -> Dict[str, bool]:
        """
        Verify if a bot is ready to trade, including WebSocket health checks.
        
        Args:
            bot_id: ID of the bot
            
        Returns:
            Dictionary with readiness checks
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Basic checks from entity
        checks = bot.can_trade()
        
        # Enhanced checks with services
        if checks["is_active"]:
            ws_healthy = True
            for account_id in bot.connected_accounts:
                try:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if not ws_status.get("connected", False):
                        ws_healthy = False
                        break
                except Exception as e:
                    logger.warning(
                        "Failed to check WebSocket status",
                        extra={"bot_id": str(bot_id), "account_id": account_id, "error": str(e)}
                    )
                    ws_healthy = False
                    break
            
            checks["ws_healthy"] = ws_healthy
            checks["ready"] = checks["ready"] and ws_healthy
        
        return checks

    @handle_db_error("Failed to record bot error", lambda self, bot_id, error: {"bot_id": str(bot_id), "error": error})
    async def record_error(self, bot_id: PydanticObjectId, error: str) -> Bot:
        """
        Record an error for a bot, potentially changing its status if error threshold is exceeded.
        
        Args:
            bot_id: ID of the bot
            error: Error message
            
        Returns:
            The updated bot
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Record error
        bot.record_error(error)
        
        # Check if error threshold is exceeded
        if bot.error_count >= trading_constants.MAX_BOT_ERRORS:
            # Deactivate bot
            bot.status = BotStatus.STOPPED
            await self._deactivate_bot_connections(bot)
            await telegram_bot.notify_bot_status(str(bot_id), bot.status)
            
            logger.warning(
                "Bot deactivated due to too many errors",
                extra={"bot_id": str(bot_id), "error_count": bot.error_count}
            )
        
        # Save changes
        await bot.save()
        
        return bot

    @handle_db_error("Failed to terminate bot positions", lambda self, bot_id: {"bot_id": str(bot_id)})
    async def terminate_positions(self, bot_id: PydanticObjectId) -> Dict[str, Any]:
        """
        Terminate all positions for all accounts connected to a bot.
        
        Args:
            bot_id: ID of the bot
            
        Returns:
            Results of termination
        """
        # Get bot
        bot = await self.get(bot_id)
        
        # Terminate positions for each account
        results = []
        for account_id in bot.connected_accounts:
            try:
                operations = await exchange_factory.get_instance(account_id)
                terminate_result = await operations.terminate_all_positions()
                results.append({
                    "account_id": account_id,
                    "success": terminate_result.get("success", False),
                    "terminated": terminate_result.get("terminated_positions", 0)
                })
            except Exception as e:
                results.append({
                    "account_id": account_id,
                    "success": False,
                    "error": str(e)
                })
        
        # Update bot status to paused
        bot.status = BotStatus.PAUSED
        bot.touch()
        await bot.save()
        
        # Calculate summary
        success_count = sum(1 for r in results if r.get("success"))
        error_count = len(results) - success_count
        
        logger.info("Terminated bot positions", extra={
            "bot_id": str(bot_id),
            "success_count": success_count,
            "error_count": error_count
        })
        
        return {
            "success": error_count == 0,
            "accounts_processed": len(results),
            "success_count": success_count,
            "error_count": error_count,
            "results": results
        }


# Create singleton instance for use throughout the application
bot = CRUDBot(Bot)