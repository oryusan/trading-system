from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.bot import Bot 
from app.models.account import Account
from app.models.daily_performance import DailyPerformance
from app.core.errors import (
    DatabaseError,
    ValidationError,
    NotFoundError
)
from app.core.references import (
    TimeFrame,
    BotStatus,
    ModelState
)

logger = get_logger(__name__)

class BotCreate(BaseModel):
    """
    Schema for creating a new bot.
    
    Validates:
    - Name format matches base_name-timeframe
    - Valid timeframe
    - Initial account assignments
    """
    name: str
    base_name: str
    timeframe: TimeFrame
    status: BotStatus = BotStatus.STOPPED
    connected_accounts: List[str] = []

    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str, info) -> str:
        if not v.startswith(f"{info.data.get('base_name')}-"):
            raise ValidationError(
                "Bot name must start with base_name",
                context={
                    "name": v,
                    "base_name": info.data.get("base_name")
                }
            )
        return v

class BotUpdate(BaseModel):
    """Schema for updating a bot."""
    name: Optional[str] = None
    base_name: Optional[str] = None
    timeframe: Optional[TimeFrame] = None
    status: Optional[BotStatus] = None
    connected_accounts: Optional[List[str]] = None

class CRUDBot(CRUDBase[Bot, BotCreate, BotUpdate]):
    """
    CRUD operations for Bot model with enhanced validation.
    
    Features:
    - Bot state management
    - Account connection validation
    - Performance tracking
    - Reference integrity
    """

    async def get_by_name(self, name: str) -> Bot:
        """Get bot by unique name."""
        try:
            bot = await Bot.find_one({"name": name})
            if not bot:
                raise NotFoundError(
                    "Bot not found",
                    context={"name": name}
                )
            return bot
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve bot by name",
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
        """Check if bot name is unique."""
        try:
            query = {"name": name}
            if exclude_id:
                query["_id"] = {"$ne": exclude_id}
            return not await Bot.find_one(query)
        except Exception as e:
            raise DatabaseError(
                "Failed to validate bot name uniqueness",
                context={
                    "name": name,
                    "exclude_id": str(exclude_id) if exclude_id else None,
                    "error": str(e)
                }
            )

    async def validate_accounts(self, account_ids: List[str]) -> None:
        """Validate accounts exist and are available."""
        try:
            seen_accounts = set()
            for account_id in account_ids:
                if account_id in seen_accounts:
                    raise ValidationError(
                        "Duplicate account ID",
                        context={"account_id": account_id}
                    )
                seen_accounts.add(account_id)

                # Validate using reference manager
                valid = await reference_manager.validate_reference(
                    source_type="Bot",
                    target_type="Account",
                    reference_id=account_id
                )
                if not valid:
                    raise ValidationError(
                        "Invalid account reference",
                        context={"account_id": account_id}
                    )

                # Check if account already connected
                account = await Account.get(account_id)
                if account.bot_id:
                    raise ValidationError(
                        "Account already connected to another bot",
                        context={
                            "account_id": account_id,
                            "bot_id": account.bot_id
                        }
                    )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to validate accounts",
                context={
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def create(self, obj_in: BotCreate) -> Bot:
        """Create new bot with validation."""
        try:
            # Validate unique name
            if not await self.validate_name_unique(obj_in.name):
                raise ValidationError(
                    "Bot name already exists",
                    context={"name": obj_in.name}
                )

            # Validate accounts if provided
            if obj_in.connected_accounts:
                await self.validate_accounts(obj_in.connected_accounts)

            db_obj = Bot(**obj_in.model_dump())
            await db_obj.insert()

            # Connect accounts
            if obj_in.connected_accounts:
                await self.connect_accounts(db_obj.id, obj_in.connected_accounts)

            logger.info(
                "Created new bot",
                extra={
                    "bot_id": str(db_obj.id),
                    "name": db_obj.name,
                    "account_count": len(obj_in.connected_accounts)
                }
            )

            return db_obj

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to create bot",
                context={
                    "name": obj_in.name,
                    "error": str(e)
                }
            )

    async def connect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        """Connect accounts to bot."""
        try:
            bot = await self.get(bot_id)
            await self.validate_accounts(account_ids)

            # Update account references
            for account_id in account_ids:
                account = await Account.get(account_id)
                account.bot_id = str(bot_id)
                await account.save()

            # Update bot
            bot.connected_accounts = account_ids
            await bot.save()

            # Initialize WebSocket connections if bot active
            if bot.status == BotStatus.ACTIVE:
                for account_id in account_ids:
                    await ws_manager.create_connection(account_id)

            logger.info(
                "Connected accounts to bot",
                extra={
                    "bot_id": str(bot_id),
                    "account_count": len(account_ids)
                }
            )

            return bot

        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to connect accounts",
                context={
                    "bot_id": str(bot_id),
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def disconnect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        """Disconnect accounts from bot."""
        try:
            bot = await self.get(bot_id)

            # Update accounts
            for account_id in account_ids:
                account = await Account.get(account_id)
                if account and account.bot_id == str(bot_id):
                    account.bot_id = None
                    await account.save()

                    # Close WebSocket if active
                    if bot.status == BotStatus.ACTIVE:
                        await ws_manager.close_connection(account_id)

            # Update bot
            bot.connected_accounts = [
                acc_id for acc_id in bot.connected_accounts
                if acc_id not in account_ids
            ]
            await bot.save()

            logger.info(
                "Disconnected accounts from bot",
                extra={
                    "bot_id": str(bot_id),
                    "account_count": len(account_ids)
                }
            )

            return bot

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to disconnect accounts",
                context={
                    "bot_id": str(bot_id),
                    "account_ids": account_ids,
                    "error": str(e)
                }
            )

    async def update_status(
        self,
        bot_id: PydanticObjectId,
        status: BotStatus
    ) -> Bot:
        """Update bot status with WebSocket management."""
        try:
            bot = await self.get(bot_id)
            previous_status = bot.status

            # Handle WebSocket connections
            if status == BotStatus.ACTIVE and previous_status != BotStatus.ACTIVE:
                for account_id in bot.connected_accounts:
                    await ws_manager.create_connection(account_id)
            elif status != BotStatus.ACTIVE and previous_status == BotStatus.ACTIVE:
                for account_id in bot.connected_accounts:
                    await ws_manager.close_connection(account_id)

            # Update bot status
            bot.status = status
            await bot.save()

            # Notify status change
            await telegram_bot.notify_bot_status(str(bot_id), status)

            logger.info(
                "Updated bot status",
                extra={
                    "bot_id": str(bot_id),
                    "previous": previous_status,
                    "new": status
                }
            )

            return bot

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update bot status",
                context={
                    "bot_id": str(bot_id),
                    "status": status,
                    "error": str(e)
                }
            )

    async def get_performance(
        self,
        bot_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Get aggregated performance metrics for bot accounts."""
        try:
            bot = await self.get(bot_id)
            return await DailyPerformance.get_aggregated_performance(
                account_ids=bot.connected_accounts,
                start_date=start_date,
                end_date=end_date
            )
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get bot performance",
                context={
                    "bot_id": str(bot_id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def get_active_bots(self) -> List[Bot]:
        """Get all active bots."""
        try:
            return await Bot.find({"status": BotStatus.ACTIVE}).to_list()
        except Exception as e:
            raise DatabaseError(
                "Failed to get active bots",
                context={"error": str(e)}
            )

    async def get_bots_by_timeframe(self, timeframe: TimeFrame) -> List[Bot]:
        """Get all bots for a specific timeframe."""
        try:
            return await Bot.find({"timeframe": timeframe}).to_list()
        except Exception as e:
            raise DatabaseError(
                "Failed to get bots by timeframe",
                context={
                    "timeframe": timeframe,
                    "error": str(e)
                }
            )

    async def get_connected_accounts(self, bot_id: PydanticObjectId) -> List[Account]:
        """Get all accounts connected to bot."""
        try:
            bot = await self.get(bot_id)
            return await Account.find(
                {"_id": {"$in": bot.connected_accounts}}
            ).to_list()
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get connected accounts",
                context={
                    "bot_id": str(bot_id),
                    "error": str(e)
                }
            )

# Add missing methods for full functionality
    async def validate_status_transition(
        self,
        current_status: BotStatus,
        new_status: BotStatus
    ) -> bool:
        """
        Validate if status transition is allowed.
        
        Validates:
        - STOPPED -> ACTIVE
        - ACTIVE -> PAUSED, STOPPED  
        - PAUSED -> ACTIVE, STOPPED
        """
        try:
            valid_transitions = {
                BotStatus.STOPPED: [BotStatus.ACTIVE],
                BotStatus.ACTIVE: [BotStatus.PAUSED, BotStatus.STOPPED],
                BotStatus.PAUSED: [BotStatus.ACTIVE, BotStatus.STOPPED]
            }
            
            if new_status not in valid_transitions.get(current_status, []):
                raise ValidationError(
                    "Invalid status transition",
                    context={
                        "current": current_status,
                        "attempted": new_status,
                        "valid_transitions": valid_transitions.get(current_status, [])
                    }
                )
            return True
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to validate status transition",
                context={
                    "current": current_status,
                    "new": new_status,
                    "error": str(e)
                }
            )

    async def verify_trading_ready(
        self,
        bot_id: PydanticObjectId
    ) -> Dict[str, bool]:
        """
        Verify bot is ready for trading.
        
        Checks:
        - Bot is active
        - Has connected accounts
        - WebSocket connections healthy
        - No existing errors
        """
        try:
            bot = await self.get(bot_id)
            
            # Basic checks
            checks = {
                "is_active": bot.status == BotStatus.ACTIVE,
                "has_accounts": len(bot.connected_accounts) > 0,
                "ws_healthy": True,
                "no_errors": bot.error_count == 0
            }
            
            # Check WebSocket health if active
            if checks["is_active"]:
                for account_id in bot.connected_accounts:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if not ws_status.get("connected", False):
                        checks["ws_healthy"] = False
                        break
            
            checks["ready"] = all(checks.values())
            return checks
            
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to verify trading readiness",
                context={
                    "bot_id": str(bot_id),
                    "error": str(e)
                }
            )

    async def get_period_stats(
        self,
        bot_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get bot performance statistics for a period.
        
        Returns:
        - trade_count
        - win_rate
        - total_pnl
        - drawdown
        - etc.
        """
        try:
            bot = await self.get(bot_id)
            return await DailyPerformance.get_period_statistics(
                account_ids=bot.connected_accounts,
                start_date=start_date,
                end_date=end_date
            )
            
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get period statistics",
                context={
                    "bot_id": str(bot_id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def record_error(
        self,
        bot_id: PydanticObjectId,
        error: str
    ) -> Bot:
        """Track bot error state."""
        try:
            bot = await self.get(bot_id)
            bot.error_count += 1
            bot.last_error = error
            
            # Disable bot if too many errors
            if bot.error_count >= trading_constants["MAX_BOT_ERRORS"]:
                bot.status = BotStatus.STOPPED
                # Notify status change
                await telegram_bot.notify_bot_status(str(bot_id), bot.status)
                
            await bot.save()
            
            logger.warning(
                "Recorded bot error",
                extra={
                    "bot_id": str(bot_id),
                    "error_count": bot.error_count,
                    "error": error
                }
            )
            
            return bot
            
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to record bot error",
                context={
                    "bot_id": str(bot_id),
                    "error": error,
                    "error_msg": str(e)
                }
            )

# Move imports to end to avoid circular imports
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.services.telegram.service import telegram_bot

# Create singleton instance
bot = CRUDBot(Bot)