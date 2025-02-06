"""
Enhanced account CRUD operations with proper error handling.

Features:
- Input validation with rich context
- Proper error type usage
- Reference validation 
- Balance tracking
- Integration with performance service
"""

from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from beanie import PydanticObjectId
from pydantic import BaseModel, field_validator
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.account import Account
from app.models.bot import Bot 
from app.models.daily_performance import DailyPerformance
from app.core.errors import (
    DatabaseError,
    ValidationError, 
    NotFoundError,
    ExchangeError
)
from app.core.references import (
    ExchangeType,
    ModelState,
    TradeSource
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

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

class CRUDAccount(CRUDBase[Account, AccountCreate, AccountUpdate]):
    """
    CRUD operations for Account model with enhanced error handling.
    
    Features:
    - Exchange integration
    - Balance tracking
    - Reference validation
    - Performance metrics
    """

    async def get_by_api_key(self, api_key: str) -> Optional[Account]:
        """Get account by API key."""
        try:
            account = await Account.find_one({"api_key": api_key})
            if not account:
                raise NotFoundError(
                    "Account not found",
                    context={"api_key": f"{api_key[:8]}..."}
                )
            return account
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to retrieve account by API key",
                context={
                    "api_key": f"{api_key[:8]}...",
                    "error": str(e)
                }
            )

    async def validate_exchange_credentials(
        self,
        exchange: ExchangeType,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        is_testnet: bool = False
    ) -> None:
        """Validate exchange API credentials."""
        try:
            # Get exchange instance
            exchange_client = await exchange_factory.get_instance(
                exchange=exchange,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                testnet=is_testnet
            )

            # Test connection
            await exchange_client.get_balance()

        except Exception as e:
            raise ExchangeError(
                "Invalid exchange credentials",
                context={
                    "exchange": exchange,
                    "api_key": f"{api_key[:8]}...",
                    "testnet": is_testnet,
                    "error": str(e)
                }
            )

    async def validate_bot_assignment(self, bot_id: Optional[str]) -> None:
        """Validate bot reference exists."""
        if bot_id:
            valid = await reference_manager.validate_reference(
                source_type="Account",
                target_type="Bot",
                reference_id=bot_id
            )
            if not valid:
                raise ValidationError(
                    "Invalid bot reference",
                    context={"bot_id": bot_id}
                )

    async def validate_group_assignments(self, group_ids: List[str]) -> None:
        """Validate group references exist."""
        seen_groups = set()
        for group_id in group_ids:
            if group_id in seen_groups:
                raise ValidationError(
                    "Duplicate group assignment",
                    context={"group_id": group_id}
                )
            seen_groups.add(group_id)

            valid = await reference_manager.validate_reference(
                source_type="Account",
                target_type="Group",
                reference_id=group_id
            )
            if not valid:
                raise ValidationError(
                    "Invalid group reference",
                    context={"group_id": group_id}
                )

    async def create(self, obj_in: AccountCreate) -> Account:
        """Create new account with validation."""
        try:
            # Validate bot and groups if provided
            await self.validate_bot_assignment(obj_in.bot_id)
            if obj_in.group_ids:
                await self.validate_group_assignments(obj_in.group_ids)

            # Validate exchange credentials
            await self.validate_exchange_credentials(
                exchange=obj_in.exchange,
                api_key=obj_in.api_key,
                api_secret=obj_in.api_secret,
                passphrase=obj_in.passphrase,
                is_testnet=obj_in.is_testnet
            )

            # Create account
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

        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to create account",
                context={
                    "user_id": obj_in.user_id,
                    "exchange": obj_in.exchange,
                    "error": str(e)
                }
            )

    async def update_balance(
        self,
        account_id: PydanticObjectId,
        balance: Decimal,
        equity: Decimal
    ) -> Account:
        """Update account balance with performance tracking."""
        try:
            account = await self.get(account_id)

            # Validate positive values
            if balance <= 0:
                raise ValidationError(
                    "Balance must be positive",
                    context={
                        "balance": str(balance),
                        "account_id": str(account_id)
                    }
                )
            if equity <= 0:
                raise ValidationError(
                    "Equity must be positive",
                    context={
                        "equity": str(equity),
                        "account_id": str(account_id)
                    }
                )

            # Update account
            account.current_balance = balance
            account.current_equity = equity
            account.last_sync = datetime.utcnow()
            await account.save()

            # Update performance metrics
            await performance_service.update_daily_performance(
                account_id=str(account_id),
                date=datetime.utcnow(),
                metrics={
                    "balance": balance,
                    "equity": equity
                }
            )

            logger.info(
                "Updated account balance",
                extra={
                    "account_id": str(account_id),
                    "balance": str(balance),
                    "equity": str(equity)
                }
            )

            return account

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update balance",
                context={
                    "account_id": str(account_id),
                    "balance": str(balance),
                    "equity": str(equity),
                    "error": str(e)
                }
            )

    async def get_performance(
        self,
        account_id: PydanticObjectId,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict]:
        """Get account performance data."""
        try:
            account = await self.get(account_id)
            return await DailyPerformance.get_account_performance(
                account_id=str(account_id),
                start_date=start_date,
                end_date=end_date
            )
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get performance data",
                context={
                    "account_id": str(account_id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def assign_to_bot(
        self,
        account_id: PydanticObjectId,
        bot_id: str
    ) -> Account:
        """Assign account to bot."""
        try:
            account = await self.get(account_id)
            
            # Validate bot
            await self.validate_bot_assignment(bot_id)

            # Update account
            account.bot_id = bot_id
            await account.save()

            logger.info(
                "Assigned account to bot",
                extra={
                    "account_id": str(account_id),
                    "bot_id": bot_id
                }
            )

            return account

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to assign account to bot",
                context={
                    "account_id": str(account_id),
                    "bot_id": bot_id,
                    "error": str(e)
                }
            )

    async def assign_to_groups(
        self,
        account_id: PydanticObjectId,
        group_ids: List[str]
    ) -> Account:
        """Assign account to groups."""
        try:
            account = await self.get(account_id)
            
            # Validate groups
            await self.validate_group_assignments(group_ids)

            # Update account
            account.group_ids = group_ids
            await account.save()

            logger.info(
                "Assigned account to groups",
                extra={
                    "account_id": str(account_id),
                    "group_count": len(group_ids)
                }
            )

            return account

        except (ValidationError, NotFoundError):
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to assign account to groups",
                context={
                    "account_id": str(account_id),
                    "group_ids": group_ids,
                    "error": str(e)
                }
            )

    async def record_trade(
        self,
        account_id: PydanticObjectId,
        symbol: str,
        side: str,
        size: str,
        entry_price: str,
        source: TradeSource
    ) -> None:
        """Record executed trade with validation."""
        try:
            account = await self.get(account_id)

            # Create trade record
            from app.models.trade import Trade
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

            logger.info(
                "Recorded trade",
                extra={
                    "account_id": str(account_id),
                    "symbol": symbol,
                    "side": side,
                    "source": source
                }
            )

        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to record trade",
                context={
                    "account_id": str(account_id),
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

# Move imports to end to avoid circular imports
from app.services.exchange.factory import exchange_factory
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service

# Create singleton instance
account = CRUDAccount(Account)