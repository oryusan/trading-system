"""
Enhanced account CRUD operations with proper error handling.

Features:
- Input validation with rich context via Pydantic.
- Exchange credential and reference validation.
- Balance tracking and performance integration.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal

from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator

from .crud_base import CRUDBase
from app.models.entities.account import Account
from app.models.entities.bot import Bot 
from app.models.entities.daily_performance import DailyPerformance
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, ExchangeError
from app.core.references import ExchangeType, TradeSource
from app.core.logging.logger import get_logger

# External services (resolved at end to avoid circular imports)
from app.services.exchange.factory import exchange_factory
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.crud.decorators import handle_db_error

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
    group_ids: List[str] = []

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

# ----------------------------
# CRUD Operations for Account
# ----------------------------

class CRUDAccount(CRUDBase[Account, AccountCreate, AccountUpdate]):
    """
    CRUD operations for the Account model with enhanced error handling.
    """

    @handle_db_error("Failed to retrieve account by API key", lambda self, api_key: {"api_key": f"{api_key[:8]}..."})
    async def get_by_api_key(self, api_key: str) -> Account:
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
    ) -> None:
        exchange_client = await exchange_factory.get_instance(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=is_testnet
        )
        await exchange_client.get_balance()

    @handle_db_error("Failed to validate bot assignment", lambda self, bot_id: {"bot_id": bot_id} if bot_id else {})
    async def validate_bot_assignment(self, bot_id: Optional[str]) -> None:
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
        await self.validate_bot_assignment(obj_in.bot_id)
        if obj_in.group_ids:
            await self.validate_group_assignments(obj_in.group_ids)
        await self.validate_exchange_credentials(
            exchange=obj_in.exchange,
            api_key=obj_in.api_key,
            api_secret=obj_in.api_secret,
            passphrase=obj_in.passphrase,
            is_testnet=obj_in.is_testnet
        )
        db_obj = Account(
            **obj_in.model_dump(),
            current_balance=obj_in.initial_balance,
            current_equity=obj_in.initial_balance,
            created_at=datetime.utcnow()
        )
        await db_obj.insert()
        logger.info(
            "Created new account",
            extra={
                "account_id": str(db_obj.id),
                "user_id": obj_in.user_id,
                "exchange": obj_in.exchange
            }
        )
        return db_obj

    def _validate_positive(self, value: Decimal, field_name: str, account_id: str) -> None:
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
        account = await self.get(account_id)
        self._validate_positive(balance, "balance", str(account_id))
        self._validate_positive(equity, "equity", str(account_id))
        account.current_balance = balance
        account.current_equity = equity
        account.last_sync = datetime.utcnow()
        await account.save()
        await performance_service.update_daily_performance(
            account_id=str(account_id),
            date=datetime.utcnow(),
            metrics={"balance": balance, "equity": equity}
        )
        logger.info(
            "Updated account balance",
            extra={"account_id": str(account_id), "balance": str(balance), "equity": str(equity)}
        )
        return account

    @handle_db_error("Failed to get performance data", lambda self, account_id, start_date, end_date: {"account_id": str(account_id), "date_range": f"{start_date} to {end_date}"})
    async def get_performance(
        self,
        account_id: PydanticObjectId,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        await self.get(account_id)
        return await DailyPerformance.get_account_performance(
            account_id=str(account_id),
            start_date=start_date,
            end_date=end_date
        )

    @handle_db_error("Failed to assign account to bot", lambda self, account_id, bot_id: {"account_id": str(account_id), "bot_id": bot_id})
    async def assign_to_bot(
        self,
        account_id: PydanticObjectId,
        bot_id: str
    ) -> Account:
        account = await self.get(account_id)
        await self.validate_bot_assignment(bot_id)
        account.bot_id = bot_id
        await account.save()
        logger.info("Assigned account to bot", extra={"account_id": str(account_id), "bot_id": bot_id})
        return account

    @handle_db_error("Failed to assign account to groups", lambda self, account_id, group_ids: {"account_id": str(account_id), "group_ids": group_ids})
    async def assign_to_groups(
        self,
        account_id: PydanticObjectId,
        group_ids: List[str]
    ) -> Account:
        account = await self.get(account_id)
        await self.validate_group_assignments(group_ids)
        account.group_ids = group_ids
        await account.save()
        logger.info("Assigned account to groups", extra={"account_id": str(account_id), "group_count": len(group_ids)})
        return account

    @handle_db_error("Failed to record trade", lambda self, account_id, symbol, side, size, entry_price, source: {"account_id": str(account_id), "symbol": symbol, "side": side})
    async def record_trade(
        self,
        account_id: PydanticObjectId,
        symbol: str,
        side: str,
        size: str,
        entry_price: str,
        source: TradeSource
    ) -> None:
        account = await self.get(account_id)
        from ..models.entities.trade import Trade  # local import to avoid circular dependency
        trade = Trade(
            account_id=str(account_id),
            exchange=account.exchange,
            symbol=symbol,
            side=side,
            size=Decimal(size),
            entry_price=Decimal(entry_price),
            source=source,
            created_at=datetime.utcnow()
        )
        await trade.insert()
        logger.info("Recorded trade", extra={"account_id": str(account_id), "symbol": symbol, "side": side})

# Create singleton instance for use elsewhere in your app
account = CRUDAccount(Account)
