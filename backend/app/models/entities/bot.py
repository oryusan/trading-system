"""
Bot model focused on data structure, validation, and core business rules.

This model avoids direct service integration and focuses on entity state,
core validation, and proper data modeling.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any, Set

from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator, model_validator

from app.core.errors.base import ValidationError
from app.core.references import BotStatus, BotType, TimeFrame, ModelState, TradeSource
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class Bot(Document):
    """
    Bot model representing a trading bot with connection to multiple accounts.
    
    This model focuses purely on data structure and validation rules,
    with no direct service dependencies.
    """

    # Core fields
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
    bot_type: BotType = Field(
        BotType.AUTOMATED,
        description="Type of bot (automated or manual)"
    )
    connected_accounts: List[str] = Field(
        default_factory=list,
        description="Connected account IDs"
    )

    # Performance metrics
    total_signals: int = Field(0, description="Total signals processed")
    successful_signals: int = Field(0, description="Successfully executed signals")
    failed_signals: int = Field(0, description="Failed signal executions")
    total_positions: int = Field(0, description="Total positions taken")
    successful_positions: int = Field(0, description="Number of profitable positions")
    
    # WebSocket tracking (state only, no service calls)
    ws_connected: bool = Field(False, description="WebSocket connection status")
    subscribed_accounts: List[str] = Field(
        default_factory=list,
        description="Accounts with active subscriptions"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Bot creation timestamp")
    modified_at: Optional[datetime] = Field(None, description="Last modification timestamp")
    last_signal: Optional[datetime] = Field(None, description="Last signal timestamp")
    
    # Error tracking
    last_error: Optional[str] = Field(None, description="Last error message")
    error_count: int = Field(0, description="Consecutive error count")

    # Configuration parameters
    max_drawdown: float = Field(60.0, description="Maximum allowed drawdown percentage", gt=0, le=100)
    risk_limit: float = Field(6.0, description="Maximum risk per trade percentage", gt=0, le=100)
    max_allocation: float = Field(369000.0, description="Maximum total allocation across accounts", gt=0)
    min_account_balance: float = Field(100.0, description="Minimum required account balance", gt=0)

    class Settings:
        """Collection settings and indexes."""
        name = "bots"
        indexes = [
            "name",
            "base_name",
            "timeframe",
            "status",
            "bot_type",
            "connected_accounts",
            "created_at",
            [("base_name", 1), ("timeframe", 1)],
            [("bot_type", 1), ("status", 1)]
        ]

    @field_validator("name")
    @classmethod
    def validate_bot_name(cls, v: str) -> str:
        """Validate bot name format."""
        if not v or not v.strip():
            raise ValidationError("Bot name cannot be empty", context={"name": v})
        
        name = v.strip()
        return name
    
    @model_validator(mode='after')
    def validate_name_format(self) -> 'Bot':
        """
        Validate that the name follows the expected format of base_name-timeframe.
        Manual trading bots are exempt from this validation.
        """
        # Skip validation for manual trading bots
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

    def touch(self) -> None:
        """Update the modified timestamp."""
        self.modified_at = datetime.utcnow()
        
    def is_valid_status_transition(self, new_status: BotStatus) -> bool:
        """
        Check if a status transition is valid based on current status.
        
        Args:
            new_status: The target status
            
        Returns:
            Boolean indicating if the transition is valid
        """
        valid_transitions = {
            BotStatus.STOPPED: [BotStatus.ACTIVE],
            BotStatus.ACTIVE: [BotStatus.PAUSED, BotStatus.STOPPED],
            BotStatus.PAUSED: [BotStatus.ACTIVE, BotStatus.STOPPED]
        }
        
        return new_status in valid_transitions.get(self.status, [])

    def record_signal_result(self, success_count: int, error_count: int) -> None:
        """
        Record the results of a signal execution.
        
        Args:
            success_count: Number of successful executions
            error_count: Number of failed executions
        """
        self.total_signals += 1
        self.successful_signals += success_count
        self.failed_signals += error_count
        self.last_signal = datetime.utcnow()
        self.touch()

    def record_error(self, error_message: str) -> None:
        """
        Record an error with the bot.
        
        Args:
            error_message: Description of the error
        """
        self.error_count += 1
        self.last_error = error_message
        self.touch()
    
    def reset_error_state(self) -> None:
        """Reset the error tracking state."""
        self.error_count = 0
        self.last_error = None
        self.touch()
    
    def update_subscription_status(self, account_id: str, is_subscribed: bool) -> None:
        """
        Update the subscription status for an account.
        
        Args:
            account_id: The account to update
            is_subscribed: Whether the account is subscribed
        """
        if is_subscribed:
            if account_id not in self.subscribed_accounts:
                self.subscribed_accounts.append(account_id)
        else:
            if account_id in self.subscribed_accounts:
                self.subscribed_accounts.remove(account_id)
        self.touch()

    def can_trade(self) -> Dict[str, bool]:
        """
        Check if the bot is ready to trade based on its current state.
        
        Returns:
            Dictionary with various readiness checks
        """
        checks = {
            "is_active": self.status == BotStatus.ACTIVE,
            "has_accounts": len(self.connected_accounts) > 0,
            "not_errored": self.error_count < 5,
            "has_subscriptions": len(self.subscribed_accounts) > 0
        }
        
        checks["ready"] = all(checks.values())
        return checks

    def to_dict(self) -> ModelState:
        """Convert to a dictionary format for API responses."""
        return {
            "bot_info": {
                "id": str(self.id),
                "name": self.name,
                "base_name": self.base_name,
                "timeframe": self.timeframe.value,
                "status": self.status.value,
                "bot_type": self.bot_type.value,
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
        """String representation of the bot."""
        return (
            f"Bot(name='{self.name}', type={self.bot_type.value}, status={self.status}, "
            f"accounts={len(self.connected_accounts)}, max_drawdown={self.max_drawdown}, "
            f"risk_limit={self.risk_limit})"
        )