from typing import List, Optional, Dict, Any
import asyncio
from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator

from app.crud.crud_base import CRUDBase
from app.models.entities.bot import Bot
from app.models.entities.account import Account
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError
from app.core.references import TimeFrame, BotStatus
from app.core.logging.logger import get_logger
from app.crud.decorators import handle_db_error

logger = get_logger(__name__)

class BotCreate(BaseModel):
    """
    Schema for creating a new bot.
    """
    name: str
    base_name: str
    timeframe: TimeFrame
    status: BotStatus = BotStatus.STOPPED
    connected_accounts: List[str] = []
    max_drawdown: Optional[float] = Field(60.0, description="Maximum allowed drawdown percentage")
    risk_limit: Optional[float] = Field(6.0, description="Maximum risk per trade percentage")
    max_allocation: Optional[float] = Field(100000.0, description="Maximum total allocation allowed")
    min_account_balance: Optional[float] = Field(100.0, description="Minimum required account balance")

    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str, info) -> str:
        base_name = info.data.get("base_name")
        if not v.startswith(f"{base_name}-"):
            raise ValidationError(
                "Bot name must start with base_name",
                context={"name": v, "base_name": base_name}
            )
        return v

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
    CRUD operations for the Bot model with enhanced validation.
    """

    @handle_db_error("Failed to retrieve bot by name", lambda self, name: {"name": name})
    async def get_by_name(self, name: str) -> Bot:
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
        query = {"name": name}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        return not await Bot.find_one(query)

    @handle_db_error("Failed to validate accounts", lambda self, account_ids: {"account_ids": account_ids})
    async def validate_accounts(self, account_ids: List[str]) -> None:
        if len(account_ids) != len(set(account_ids)):
            duplicates = list({acc for acc in account_ids if account_ids.count(acc) > 1})
            raise ValidationError("Duplicate account IDs found", context={"duplicates": duplicates})
        for account_id in account_ids:
            valid = await reference_manager.validate_reference(
                source_type="Bot",
                target_type="Account",
                reference_id=account_id
            )
            if not valid:
                raise ValidationError("Invalid account reference", context={"account_id": account_id})
            account = await Account.get(account_id)
            if account.bot_id:
                raise ValidationError(
                    "Account already connected to another bot",
                    context={"account_id": account_id, "bot_id": account.bot_id}
                )

    @handle_db_error("Failed to create bot", lambda self, obj_in: {"name": obj_in.name})
    async def create(self, obj_in: BotCreate) -> Bot:
        if not await self.validate_name_unique(obj_in.name):
            raise ValidationError("Bot name already exists", context={"name": obj_in.name})
        if obj_in.connected_accounts:
            await self.validate_accounts(obj_in.connected_accounts)
        db_obj = Bot(
            name=obj_in.name,
            base_name=obj_in.base_name,
            timeframe=obj_in.timeframe,
            status=obj_in.status,
            connected_accounts=obj_in.connected_accounts,
            max_drawdown=obj_in.max_drawdown if obj_in.max_drawdown is not None else 25.0,
            risk_limit=obj_in.risk_limit if obj_in.risk_limit is not None else 5.0,
            max_allocation=obj_in.max_allocation if obj_in.max_allocation is not None else 100000.0,
            min_account_balance=obj_in.min_account_balance if obj_in.min_account_balance is not None else 100.0
        )
        await db_obj.insert()
        if obj_in.connected_accounts:
            await self.connect_accounts(db_obj.id, obj_in.connected_accounts)
        logger.info("Created new bot", extra={
            "bot_id": str(db_obj.id),
            "name": db_obj.name,
            "account_count": len(obj_in.connected_accounts)
        })
        return db_obj

    @handle_db_error("Failed to update bot", lambda self, bot_id, updates: {"bot_id": str(bot_id), "fields": list(updates.keys())})
    async def update(self, bot_id: PydanticObjectId, obj_in: BotUpdate) -> Bot:
        db_obj = await self.get(bot_id)
        update_data = obj_in.model_dump(exclude_unset=True)
        # Optionally, validate new values here if needed.
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        await db_obj.save()
        logger.info("Updated bot", extra={"bot_id": str(bot_id), "fields": list(update_data.keys())})
        return db_obj

    @handle_db_error("Failed to connect accounts", lambda self, bot_id, account_ids: {"bot_id": str(bot_id), "account_ids": account_ids})
    async def connect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        bot = await self.get(bot_id)
        await self.validate_accounts(account_ids)
        async def update_account(account_id: str):
            account = await Account.get(account_id)
            account.bot_id = str(bot_id)
            await account.save()
            if bot.status == BotStatus.ACTIVE:
                await ws_manager.create_connection(account_id)
        await asyncio.gather(*(update_account(acc_id) for acc_id in account_ids))
        bot.connected_accounts = account_ids
        await bot.save()
        logger.info("Connected accounts to bot", extra={
            "bot_id": str(bot_id),
            "account_count": len(account_ids)
        })
        return bot

    @handle_db_error("Failed to disconnect accounts", lambda self, bot_id, account_ids: {"bot_id": str(bot_id), "account_ids": account_ids})
    async def disconnect_accounts(
        self,
        bot_id: PydanticObjectId,
        account_ids: List[str]
    ) -> Bot:
        bot = await self.get(bot_id)
        async def disconnect_account(account_id: str):
            account = await Account.get(account_id)
            if account and account.bot_id == str(bot_id):
                account.bot_id = None
                await account.save()
                if bot.status == BotStatus.ACTIVE:
                    await ws_manager.close_connection(account_id)
        await asyncio.gather(*(disconnect_account(acc_id) for acc_id in account_ids))
        bot.connected_accounts = [acc_id for acc_id in bot.connected_accounts if acc_id not in account_ids]
        await bot.save()
        logger.info("Disconnected accounts from bot", extra={
            "bot_id": str(bot_id),
            "account_count": len(account_ids)
        })
        return bot

    @handle_db_error("Failed to update bot status", lambda self, bot_id, status: {"bot_id": str(bot_id), "status": status})
    async def update_status(
        self,
        bot_id: PydanticObjectId,
        status: BotStatus
    ) -> Bot:
        bot = await self.get(bot_id)
        previous_status = bot.status

        if status == BotStatus.ACTIVE and previous_status != BotStatus.ACTIVE:
            await asyncio.gather(*(ws_manager.create_connection(acc_id) for acc_id in bot.connected_accounts))
        elif status == BotStatus.PAUSED:
            if previous_status == BotStatus.ACTIVE:
                logger.info("Bot paused; retaining connections for quick resume", extra={"bot_id": str(bot_id)})
        elif status == BotStatus.STOPPED:
            if previous_status == BotStatus.ACTIVE:
                await asyncio.gather(*(ws_manager.close_connection(acc_id) for acc_id in bot.connected_accounts))

        bot.status = status
        bot.modified_at = datetime.utcnow()
        await bot.save()
        await telegram_bot.notify_bot_status(str(bot_id), status)
        logger.info("Updated bot status", extra={"bot_id": str(bot_id), "previous": previous_status, "new": status})
        return bot

    @handle_db_error("Failed to get bot performance", lambda self, bot_id, start_date, end_date: {"bot_id": str(bot_id), "date_range": f"{start_date} to {end_date}"})
    async def get_performance(
        self,
        bot_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        bot = await self.get(bot_id)
        return await DailyPerformance.get_aggregated_performance(
            account_ids=bot.connected_accounts,
            start_date=start_date,
            end_date=end_date
        )

    @handle_db_error("Failed to get active bots", lambda self: {})
    async def get_active_bots(self) -> List[Bot]:
        return await Bot.find({"status": BotStatus.ACTIVE}).to_list()

    @handle_db_error("Failed to get bots by timeframe", lambda self, timeframe: {"timeframe": timeframe})
    async def get_bots_by_timeframe(self, timeframe: TimeFrame) -> List[Bot]:
        return await Bot.find({"timeframe": timeframe}).to_list()

    @handle_db_error("Failed to get connected accounts", lambda self, bot_id: {"bot_id": str(bot_id)})
    async def get_connected_accounts(self, bot_id: PydanticObjectId) -> List[Account]:
        bot = await self.get(bot_id)
        return await Account.find({"_id": {"$in": bot.connected_accounts}}).to_list()

    @handle_db_error("Failed to validate status transition", lambda self, current_status, new_status: {"current": current_status, "new": new_status})
    async def validate_status_transition(
        self,
        current_status: BotStatus,
        new_status: BotStatus
    ) -> bool:
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

    @handle_db_error("Failed to verify trading readiness", lambda self, bot_id: {"bot_id": str(bot_id)})
    async def verify_trading_ready(
        self,
        bot_id: PydanticObjectId
    ) -> Dict[str, bool]:
        bot = await self.get(bot_id)
        checks = {
            "is_active": bot.status == BotStatus.ACTIVE,
            "has_accounts": bool(bot.connected_accounts),
            "ws_healthy": True,
            "no_errors": getattr(bot, "error_count", 0) == 0
        }
        if checks["is_active"]:
            for account_id in bot.connected_accounts:
                ws_status = await ws_manager.get_connection_status(account_id)
                if not ws_status.get("connected", False):
                    checks["ws_healthy"] = False
                    break
        checks["ready"] = all(checks.values())
        return checks

    @handle_db_error("Failed to get period statistics", lambda self, bot_id, start_date, end_date: {"bot_id": str(bot_id), "date_range": f"{start_date} to {end_date}"})
    async def get_period_stats(
        self,
        bot_id: PydanticObjectId,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        bot = await self.get(bot_id)
        return await DailyPerformance.get_period_statistics(
            account_ids=bot.connected_accounts,
            start_date=start_date,
            end_date=end_date
        )

    @handle_db_error("Failed to record bot error", lambda self, bot_id, error: {"bot_id": str(bot_id), "error": error})
    async def record_error(
        self,
        bot_id: PydanticObjectId,
        error: str
    ) -> Bot:
        bot = await self.get(bot_id)
        bot.error_count = getattr(bot, "error_count", 0) + 1
        bot.last_error = error

        if bot.error_count >= trading_constants.MAX_BOT_ERRORS:
            bot.status = BotStatus.STOPPED
            await telegram_bot.notify_bot_status(str(bot_id), bot.status)

        await bot.save()
        logger.warning("Recorded bot error", extra={
            "bot_id": str(bot_id),
            "error_count": bot.error_count,
            "error": error
        })
        return bot

# Import external services to avoid circular dependencies
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.services.telegram.service import telegram_bot
from app.core.config.constants import trading_constants

# Create a singleton instance for external use
bot = CRUDBot(Bot)
