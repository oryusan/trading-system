from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from beanie import Document, before_event, Insert, Replace, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import DatabaseError, ValidationError, NotFoundError, ExchangeError
from app.core.references import ExchangeType, TradeSource, ModelState
from app.core.logging.logger import get_logger

if TYPE_CHECKING:
    # For type hints only
    from app.models.entities.group import AccountGroup

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


class Account(Document):
    """
    Account model with enhanced error handling, performance tracking,
    and reference validation.
    """
    user_id: Indexed(str) = Field(..., description="ID of the account owner")
    exchange: ExchangeType = Field(..., description="Exchange this account trades on")
    name: str = Field(..., description="Account display name")
    api_key: str = Field(..., description="Exchange API key")
    api_secret: str = Field(..., description="Exchange API secret")
    passphrase: Optional[str] = Field(None, description="Optional API passphrase")
    bot_id: Optional[str] = Field(None, description="Active bot reference")
    group_ids: List[str] = Field(default_factory=list, description="Associated group IDs")
    initial_balance: Decimal = Field(..., description="Initial balance")
    current_balance: Decimal = Field(..., description="Current available balance")
    current_equity: Decimal = Field(..., description="Current total equity")
    open_positions: int = Field(0, description="Current open positions")
    total_positions: int = Field(0, description="Total positions taken")
    successful_positions: int = Field(0, description="Profitable positions")
    position_value: Decimal = Field(Decimal("0"), description="Total position value")
    is_testnet: bool = Field(False, description="Using testnet")
    is_active: bool = Field(True, description="Account enabled")
    max_positions: int = Field(5, description="Max concurrent positions")
    max_drawdown: float = Field(25.0, description="Max drawdown percentage")
    trading_fees: Decimal = Field(Decimal("0"), description="Accumulated trading fees")
    funding_fees: Decimal = Field(Decimal("0"), description="Accumulated funding fees")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    modified_at: Optional[datetime] = Field(None, description="Last modified timestamp")
    last_sync: Optional[datetime] = Field(None, description="Last balance sync")
    last_error: Optional[str] = Field(None, description="Last error message")
    error_count: int = Field(0, description="Consecutive errors")

    class Settings:
        name = "accounts"
        indexes = [
            "user_id",
            "exchange",
            "bot_id",
            "group_ids",
            "is_active",
            "created_at",
            [("exchange", 1), ("is_active", 1)],
            [("bot_id", 1), ("is_active", 1)]
        ]

    @field_validator("current_balance", "current_equity", "initial_balance")
    @classmethod
    def validate_balance(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValidationError("Balance must be positive", context={"value": str(value)})
        return value

    @field_validator("api_key", "api_secret")
    @classmethod
    def validate_credentials_field(cls, value: str) -> str:
        credential = value.strip()
        if not credential:
            raise ValidationError("API credential cannot be empty", context={"credential": "*****"})
        if len(credential) < 16:
            raise ValidationError("API credential too short", context={"length": len(credential), "min_length": 16})
        if len(credential) > 128:
            raise ValidationError("API credential too long", context={"length": len(credential), "max_length": 128})
        if not all(c.isalnum() or c in "-_" for c in credential):
            raise ValidationError("Invalid characters in API credential", context={"credential": credential[:8] + "..."})
        return credential

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate model relationships by checking that the referenced
        user, bot, and groups exist.
        Imports the reference manager locally to avoid circular dependencies.
        """
        try:
            from app.services.reference.manager import reference_manager
            if not await reference_manager.validate_reference("Account", "User", self.user_id):
                raise ValidationError("Invalid user reference", context={"user_id": self.user_id})
            if self.bot_id:
                if not await reference_manager.validate_reference("Account", "Bot", self.bot_id):
                    raise ValidationError("Invalid bot reference", context={"bot_id": self.bot_id})
            seen_groups = set()
            for group_id in self.group_ids:
                if group_id in seen_groups:
                    raise ValidationError("Duplicate group reference", context={"group_id": group_id})
                seen_groups.add(group_id)
                if not await reference_manager.validate_reference("Account", "Group", group_id):
                    raise ValidationError("Invalid group reference", context={"group_id": group_id})
            self.touch()
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Reference validation failed", context={"account_id": str(self.id), "error": str(e)}) from e

    async def validate_credentials(self) -> None:
        """
        Validate API credentials using the trading service.
        Imports the reference manager locally to avoid circular dependencies.
        """
        try:
            from app.services.reference.manager import reference_manager
            trading_service = await reference_manager.get_service("TradingService")
            result = await trading_service.validate_credentials(
                exchange=self.exchange,
                api_key=self.api_key,
                api_secret=self.api_secret,
                passphrase=self.passphrase,
                testnet=self.is_testnet
            )
            if not result.get("valid", False):
                raise ValidationError("Invalid API credentials", context={"exchange": self.exchange, "errors": result.get("errors")})
            if self.error_count > 0:
                self.error_count = 0
                self.last_error = None
                self.is_active = True
                await self.save()
            logger.info("Validated API credentials", extra={"account_id": str(self.id), "exchange": self.exchange})
        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError("Credential validation failed", context={"account_id": str(self.id), "exchange": self.exchange, "error": str(e)}) from e

    async def sync_balance(self) -> None:
        """
        Synchronize account balance and update performance.
        Imports the reference manager and performance service locally.
        """
        try:
            from app.services.reference.manager import reference_manager
            from app.services.performance.service import performance_service
            trading_service = await reference_manager.get_service("TradingService")
            balance_info = await trading_service.get_account_balance(account_id=str(self.id))
            positions = await trading_service.get_account_positions(account_id=str(self.id))
            self.current_balance = Decimal(str(balance_info["balance"]))
            self.current_equity = Decimal(str(balance_info["equity"]))
            self.position_value = sum(Decimal(str(p["notional_value"])) for p in positions)
            self.open_positions = len(positions)
            self.last_sync = datetime.utcnow()
            self.touch()
            await self.save()
            await performance_service.update_daily_performance(
                account_id=str(self.id),
                date=datetime.utcnow(),
                metrics={"balance": self.current_balance, "equity": self.current_equity}
            )
            logger.info("Updated account balance", extra={"account_id": str(self.id), "balance": str(self.current_balance)})
        except Exception as e:
            self.error_count += 1
            self.last_error = str(e)
            if self.error_count >= 5:
                self.is_active = False
                logger.warning("Account deactivated due to errors", extra={"account_id": str(self.id), "error_count": self.error_count})
            await self.save()
            raise ExchangeError("Balance sync failed", context={"account_id": str(self.id), "exchange": self.exchange, "error_count": self.error_count, "error": str(e)}) from e

    async def check_trade_limits(self) -> Dict[str, bool]:
        """
        Check trading limits for the account.
        """
        try:
            drawdown = self._calculate_drawdown()
            min_balance = Decimal("1000")
            limits = {
                "is_active": self.is_active,
                "max_positions": self.open_positions < self.max_positions,
                "max_drawdown": drawdown < self.max_drawdown,
                "min_balance": self.current_balance >= min_balance,
                "error_threshold": self.error_count < 5
            }
            limits["can_trade"] = all(limits.values())
            if not limits["can_trade"]:
                failed_checks = [k for k, passed in limits.items() if not passed and k != "can_trade"]
                logger.warning("Account trading restricted", extra={"account_id": str(self.id), "failed_checks": failed_checks})
            return limits
        except Exception as e:
            raise ValidationError("Trade limit check failed", context={"account_id": str(self.id), "error": str(e)}) from e

    def _calculate_drawdown(self) -> float:
        try:
            if self.initial_balance <= 0:
                return 0.0
            drawdown = ((self.initial_balance - self.current_equity) / self.initial_balance) * 100
            if drawdown > self.max_drawdown * 0.8:
                logger.warning("High drawdown detected", extra={"account_id": str(self.id), "drawdown": drawdown, "max_drawdown": self.max_drawdown})
            return max(0.0, drawdown)
        except Exception as e:
            logger.error("Drawdown calculation failed", extra={"account_id": str(self.id), "error": str(e)})
            return 0.0

    async def add_to_group(self, group_id: str) -> None:
        """
        Add this account to a group.
        Imports the reference manager locally to avoid circular dependencies.
        """
        try:
            if group_id in self.group_ids:
                raise ValidationError("Account already in group", context={"group_id": group_id, "account_id": str(self.id)})
            from app.services.reference.manager import reference_manager
            await reference_manager.validate_reference("Account", "Group", group_id)
            self.group_ids.append(group_id)
            self.touch()
            await self.save()
            await reference_manager.add_reference("Account", "Group", str(self.id), group_id)
            logger.info("Added account to group", extra={"account_id": str(self.id), "group_id": group_id})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to add to group", context={"account_id": str(self.id), "group_id": group_id, "error": str(e)}) from e

    async def remove_from_group(self, group_id: str) -> None:
        """
        Remove this account from a group.
        Imports the reference manager locally to avoid circular dependencies.
        """
        try:
            if group_id not in self.group_ids:
                raise ValidationError("Account not in group", context={"group_id": group_id, "account_id": str(self.id)})
            self.group_ids.remove(group_id)
            self.touch()
            await self.save()
            from app.services.reference.manager import reference_manager
            await reference_manager.remove_reference("Account", "Group", str(self.id), group_id)
            logger.info("Removed account from group", extra={"account_id": str(self.id), "group_id": group_id})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to remove from group", context={"account_id": str(self.id), "group_id": group_id, "error": str(e)}) from e

    @lazy_handle_db_error(
        "Failed to get position history",
        lambda self, start_date, end_date: {"account_id": str(self.id), "date_range": f"{start_date} to {end_date}"}
    )
    async def get_position_history(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the account's position history based solely on closed, finalized trades.
        Imports DailyPerformance locally to avoid circular dependencies.
        """
        from app.models.entities.daily_performance import DailyPerformance
        return await DailyPerformance.get_account_performance(
            account_id=str(self.id),
            start_date=start_date,
            end_date=end_date
        )

    def to_dict(self) -> ModelState:
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
