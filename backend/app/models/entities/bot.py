"""
Bot model with enhanced error handling and service integration.

Features:
- Signal routing to exchange operations
- WebSocket connection management
- Performance tracking
- Enhanced error handling
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator
from app.core.references import BotStatus, TimeFrame, ModelState, TradeSource

class Bot(Document):
    name: Indexed(str, unique=True) = Field(
        ...,
        description="Unique bot name (format: BotA-1m)"
    )
    base_name: Indexed(str) = Field(
        ...,
        description="Base strategy name (e.g. BotA)"
    )
    timeframe: TimeFrame = Field(
        ...,
        description="Trading timeframe"
    )
    status: BotStatus = Field(
        BotStatus.STOPPED,
        description="Current operational status"
    )
    connected_accounts: List[str] = Field(
        default_factory=list,
        description="Connected account IDs"
    )
    total_signals: int = Field(0, description="Total signals processed")
    successful_signals: int = Field(0, description="Successfully executed signals")
    failed_signals: int = Field(0, description="Failed signal executions")
    total_positions: int = Field(0, description="Total positions taken")
    successful_positions: int = Field(0, description="Number of profitable positions")
    ws_connected: bool = Field(False, description="WebSocket connection status")
    subscribed_accounts: List[str] = Field(
        default_factory=list,
        description="Accounts with active subscriptions"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Bot creation timestamp")
    modified_at: Optional[datetime] = Field(None, description="Last modification timestamp")
    last_signal: Optional[datetime] = Field(None, description="Last signal timestamp")
    last_error: Optional[str] = Field(None, description="Last error message")
    error_count: int = Field(0, description="Consecutive error count")

    # New performance/capacity configuration fields
    max_drawdown: float = Field(60.0, description="Maximum allowed drawdown percentage", gt=0, le=100)
    risk_limit: float = Field(6.0, description="Maximum risk per trade percentage", gt=0, le=100)
    max_allocation: float = Field(369000.0, description="Maximum total allocation across accounts", gt=0)
    min_account_balance: float = Field(100.0, description="Minimum required account balance", gt=0)

    class Settings:
        name = "bots"
        indexes = [
            "name",
            "base_name",
            "timeframe",
            "status",
            "connected_accounts",
            "created_at",
            [("base_name", 1), ("timeframe", 1)]
        ]

    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValidationError("Bot name cannot be empty", context={"name": v})
        name = v.strip()
        base_name = info.data.get("base_name")
        timeframe = info.data.get("timeframe")
        if base_name and timeframe:
            expected = f"{base_name}-{timeframe}"
            if name != expected:
                raise ValidationError("Invalid bot name format", context={"name": name, "expected": expected, "base_name": base_name, "timeframe": timeframe})
        return name

    def touch(self) -> None:
        self.modified_at = datetime.utcnow()

    async def _validate_account_ref(self, account_id: str) -> None:
        """Helper method to validate an account reference for the bot."""
        valid = await reference_manager.validate_reference(
            source_type="Bot",
            target_type="Account",
            reference_id=account_id
        )
        if not valid:
            raise ValidationError("Invalid account reference", context={"account_id": account_id})

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate model references and check that for active bots each connected account
        has an active WebSocket connection.
        """
        seen_accounts = set()
        for account_id in self.connected_accounts:
            if account_id in seen_accounts:
                raise ValidationError("Duplicate account reference", context={"account_id": account_id})
            seen_accounts.add(account_id)
            await self._validate_account_ref(account_id)
            if self.status == BotStatus.ACTIVE:
                try:
                    ws_status = await ws_manager.get_connection_status(account_id)
                    if not ws_status.get("connected", False):
                        raise ValidationError("WebSocket not connected for active bot", context={"account_id": account_id, "ws_status": ws_status})
                except Exception as e:
                    logger.warning("Failed to verify WebSocket", extra={"account_id": account_id, "error": str(e)})
        self.touch()

    async def connect_account(self, account_id: str) -> None:
        try:
            await reference_manager.add_reference(
                source_type="Bot",
                target_type="Account",
                source_id=str(self.id),
                target_id=account_id
            )
            if self.status == BotStatus.ACTIVE:
                await ws_manager.create_connection(account_id)
                for channel in ["positions", "orders", "balances"]:
                    await ws_manager.subscribe(account_id, channel)
            if account_id not in self.connected_accounts:
                self.connected_accounts.append(account_id)
                if self.status == BotStatus.ACTIVE:
                    self.subscribed_accounts.append(account_id)
                self.touch()
                await self.save()
            logger.info("Connected account to bot", extra={"bot_id": str(self.id), "account_id": account_id, "status": self.status})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to connect account", context={"bot_id": str(self.id), "account_id": account_id, "error": str(e)})

    async def disconnect_account(self, account_id: str) -> None:
        try:
            await reference_manager.remove_reference(
                source_type="Bot",
                target_type="Account",
                source_id=str(self.id),
                target_id=account_id
            )
            if account_id in self.subscribed_accounts:
                await ws_manager.close_connection(account_id)
            if account_id in self.connected_accounts:
                self.connected_accounts.remove(account_id)
            if account_id in self.subscribed_accounts:
                self.subscribed_accounts.remove(account_id)
            self.touch()
            await self.save()
            logger.info("Disconnected account from bot", extra={"bot_id": str(self.id), "account_id": account_id})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to disconnect account", context={"bot_id": str(self.id), "account_id": account_id, "error": str(e)})

    async def process_signal(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        if self.status != BotStatus.ACTIVE:
            raise ValidationError("Bot not active", context={"bot_id": str(self.id), "status": self.status})

        async def process_trade(account_id: str) -> Dict[str, Any]:
            try:
                operations = await exchange_factory.get_instance(account_id, reference_manager)
                trade_result = await operations.execute_trade(
                    account_id=account_id,
                    symbol=signal_data["symbol"],
                    side=signal_data["side"],
                    order_type=signal_data["order_type"],
                    size=signal_data["size"],
                    leverage=signal_data["leverage"],
                    take_profit=signal_data.get("take_profit"),
                    source=TradeSource.BOT
                )
                return {"account_id": account_id, "success": trade_result["success"], "details": trade_result}
            except Exception as ex:
                return {"account_id": account_id, "success": False, "error": str(ex)}

        try:
            tasks = [process_trade(account_id) for account_id in self.connected_accounts]
            results = await asyncio.gather(*tasks)
            success_count = sum(1 for r in results if r.get("success"))
            error_count = len(results) - success_count
            self.total_signals += 1
            self.successful_signals += success_count
            self.failed_signals += error_count
            self.last_signal = datetime.utcnow()
            self.touch()
            await self.save()
            logger.info("Processed signal", extra={"bot_id": str(self.id), "success_count": success_count, "error_count": error_count})
            return {
                "success": error_count == 0,
                "accounts_processed": len(results),
                "success_count": success_count,
                "error_count": error_count,
                "results": results
            }
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Signal processing failed", context={"bot_id": str(self.id), "signal": signal_data, "error": str(e)})

    async def update_status(self, new_status: BotStatus) -> None:
        valid_transitions = {
            BotStatus.STOPPED: [BotStatus.ACTIVE],
            BotStatus.ACTIVE: [BotStatus.PAUSED, BotStatus.STOPPED],
            BotStatus.PAUSED: [BotStatus.ACTIVE, BotStatus.STOPPED]
        }
        if new_status not in valid_transitions.get(self.status, []):
            raise ValidationError("Invalid status transition", context={"current": self.status, "attempted": new_status, "valid_transitions": valid_transitions.get(self.status, [])})
        try:
            old_status = self.status
            self.status = new_status
            self.touch()
            if new_status == BotStatus.ACTIVE:
                for account_id in self.connected_accounts:
                    if account_id not in self.subscribed_accounts:
                        await self._setup_account_websocket(account_id)
            elif new_status == BotStatus.STOPPED:
                for account_id in list(self.subscribed_accounts):
                    await ws_manager.close_connection(account_id)
                    self.subscribed_accounts.remove(account_id)
            await self.save()
            await telegram_bot.notify_bot_status(str(self.id), new_status)
            logger.info("Updated bot status", extra={"bot_id": str(self.id), "old_status": old_status, "new_status": new_status})
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError("Failed to update status", context={"bot_id": str(self.id), "current": self.status, "new_status": new_status, "error": str(e)})

    async def _setup_account_websocket(self, account_id: str) -> None:
        try:
            account = await reference_manager.get_reference(account_id)
            if not account:
                raise ValidationError("Account not found", context={"account_id": account_id})
            operations = await exchange_factory.get_instance(account_id, reference_manager)
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
            for channel in ["positions", "orders", "balances"]:
                await ws_manager.subscribe(account_id, channel)
            if account_id not in self.subscribed_accounts:
                self.subscribed_accounts.append(account_id)
                self.touch()
                await self.save()
            logger.info("Setup WebSocket connection", extra={"bot_id": str(self.id), "account_id": account_id})
        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError("WebSocket setup failed", context={"bot_id": str(self.id), "account_id": account_id, "error": str(e)})

    async def get_status(self) -> Dict[str, Any]:
        async def fetch_account_status(account_id: str) -> (str, Dict[str, Any]):
            try:
                operations = await exchange_factory.get_instance(account_id, reference_manager)
                positions, balance, ws_status = await asyncio.gather(
                    operations.get_all_positions(),
                    operations.get_balance(),
                    ws_manager.get_connection_status(account_id)
                )
                return account_id, {
                    "connected": ws_status.get("connected", False),
                    "positions": len(positions),
                    "balance": str(balance["balance"]),
                    "equity": str(balance["equity"]),
                    "websocket": ws_status
                }
            except Exception as e:
                return account_id, {"error": str(e), "connected": False}

        try:
            account_tasks = [fetch_account_status(account_id) for account_id in self.connected_accounts]
            account_results = await asyncio.gather(*account_tasks)
            account_status = {acc_id: status for acc_id, status in account_results}
            success_rate = (self.successful_signals / self.total_signals * 100) if self.total_signals > 0 else 0
            return {
                "bot_info": {
                    "id": str(self.id),
                    "name": self.name,
                    "base_name": self.base_name,
                    "timeframe": self.timeframe,
                    "status": self.status
                },
                "connections": {
                    "total_accounts": len(self.connected_accounts),
                    "subscribed_accounts": len(self.subscribed_accounts),
                    "account_status": account_status
                },
                "metrics": {
                    "total_signals": self.total_signals,
                    "successful_signals": self.successful_signals,
                    "failed_signals": self.failed_signals,
                    "total_positions": self.total_positions,
                    "successful_positions": self.successful_positions
                },
                "timestamps": {
                    "created_at": self.created_at.isoformat(),
                    "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                    "last_signal": self.last_signal.isoformat() if self.last_signal else None
                },
                "error_info": {
                    "error_count": self.error_count,
                    "last_error": self.last_error
                }
            }
        except Exception as e:
            raise DatabaseError("Failed to get bot status", context={"bot_id": str(self.id), "error": str(e)})

    def to_dict(self) -> ModelState:
        return {
            "bot_info": {
                "id": str(self.id),
                "name": self.name,
                "base_name": self.base_name,
                "timeframe": self.timeframe,
                "status": self.status,
                "max_drawdown": self.max_drawdown,
                "risk_limit": self.risk_limit,
                "max_allocation": self.max_allocation,
                "min_account_balance": self.min_account_balance,
            },
            "connections": {
                "connected_accounts": self.connected_accounts,
                "subscribed_accounts": self.subscribed_accounts,
                "ws_connected": self.ws_connected
            },
            "metrics": {
                "total_signals": self.total_signals,
                "successful_signals": self.successful_signals,
                "failed_signals": self.failed_signals,
                "total_positions": self.total_positions,
                "successful_positions": self.successful_positions
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_signal": self.last_signal.isoformat() if self.last_signal else None
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error
            }
        }

    def __repr__(self) -> str:
        return (
            f"Bot(name='{self.name}', status={self.status}, accounts={len(self.connected_accounts)}, "
            f"max_drawdown={self.max_drawdown}, risk_limit={self.risk_limit})"
        )


# ==== Import dependencies to avoid circular references ====
from app.core.errors.base import ValidationError, DatabaseError, WebSocketError
from app.core.logging.logger import get_logger
from app.services.exchange.factory import exchange_factory
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager
from app.services.telegram.service import telegram_bot

logger = get_logger(__name__)
