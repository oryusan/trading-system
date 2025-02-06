"""
Account model with enhanced error handling and validation.

Features:
- Exchange API management
- State validation
- Reference integrity
- Performance tracking
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional, Any
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

class Account(Document):
    """
    Account model with enhanced validation and error handling.
    
    Features:
    - Exchange API management
    - State validation
    - Reference integrity
    - Performance tracking
    """
    
    # Core fields
    user_id: Indexed(str) = Field(
        ..., 
        description="ID of the account owner"
    )
    exchange: ExchangeType = Field(
        ...,
        description="Exchange this account trades on"
    )
    name: str = Field(
        ...,
        description="Account display name"
    )
    
    # API credentials
    api_key: str = Field(
        ...,
        description="Exchange API key"
    )
    api_secret: str = Field(
        ...,
        description="Exchange API secret"
    )
    passphrase: Optional[str] = Field(
        None,
        description="Optional API passphrase"
    )
    
    # Relationships
    bot_id: Optional[str] = Field(
        None,
        description="Active bot reference"
    )
    group_ids: List[str] = Field(
        default_factory=list,
        description="Associated group IDs"
    )
    
    # Balance tracking
    initial_balance: Decimal = Field(
        ...,
        description="Initial balance"
    )
    current_balance: Decimal = Field(
        ...,
        description="Current available balance"
    )
    current_equity: Decimal = Field(
        ...,
        description="Current total equity"
    )
    
    # Position tracking  
    open_positions: int = Field(
        0,
        description="Current open positions"
    )
    total_positions: int = Field(
        0,
        description="Total positions taken"
    )
    successful_positions: int = Field(
        0,
        description="Profitable positions"
    )
    position_value: Decimal = Field(
        Decimal("0"),
        description="Total position value"
    )
    
    # Settings
    is_testnet: bool = Field(
        False,
        description="Using testnet"
    )
    is_active: bool = Field(
        True,
        description="Account enabled"
    )
    max_positions: int = Field(
        5,
        description="Max concurrent positions"
    )
    max_drawdown: float = Field(
        25.0,
        description="Max drawdown percentage"
    )
    
    # Fee tracking
    trading_fees: Decimal = Field(
        Decimal("0"),
        description="Accumulated trading fees"
    )
    funding_fees: Decimal = Field(
        Decimal("0"),
        description="Accumulated funding fees" 
    )
    
    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modified timestamp"
    )
    last_sync: Optional[datetime] = Field(
        None,
        description="Last balance sync"
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )
    error_count: int = Field(
        0,
        description="Consecutive errors"
    )

    class Settings:
        """Collection settings."""
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
    def validate_balance(cls, v: Decimal) -> Decimal:
        """Validate balance amounts are positive."""
        if v <= 0:
            raise ValidationError(
                "Balance must be positive",
                context={"value": str(v)}
            )
        return v

    @field_validator("api_key", "api_secret")
    @classmethod
    def validate_credentials(cls, v: str) -> str:
        """Validate API credential format."""
        if not v or not v.strip():
            raise ValidationError(
                "API credential cannot be empty",
                context={"credential": "*****"}
            )

        credential = v.strip()
        
        # Validate length
        if len(credential) < 16:
            raise ValidationError(
                "API credential too short",
                context={
                    "length": len(credential),
                    "min_length": 16
                }
            )

        if len(credential) > 128:
            raise ValidationError(
                "API credential too long", 
                context={
                    "length": len(credential),
                    "max_length": 128
                }
            )

        # Validate characters
        if not all(c.isalnum() or c in "-_" for c in credential):
            raise ValidationError(
                "Invalid characters in API credential",
                context={
                    "credential": credential[:8] + "..."
                }
            )

        return credential

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate model relationships.
        
        Raises:
            ValidationError: If references invalid
            DatabaseError: If validation fails
        """
        try:
            # Validate user exists
            valid = await reference_manager.validate_reference(
                source_type="Account",
                target_type="User", 
                reference_id=self.user_id
            )
            if not valid:
                raise ValidationError(
                    "Invalid user reference",
                    context={"user_id": self.user_id}
                )

            # Validate active bot if assigned
            if self.bot_id:
                valid = await reference_manager.validate_reference(
                    source_type="Account",
                    target_type="Bot",
                    reference_id=self.bot_id
                )
                if not valid:
                    raise ValidationError(
                        "Invalid bot reference",
                        context={"bot_id": self.bot_id}
                    )

            # Validate groups
            seen_groups = set()
            for group_id in self.group_ids:
                if group_id in seen_groups:
                    raise ValidationError(
                        "Duplicate group reference",
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

            self.modified_at = datetime.utcnow()

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "account_id": str(self.id),
                    "error": str(e)
                }
            )

    async def validate_credentials(self) -> None:
        """
        Validate exchange API credentials.
        
        Raises:
            ValidationError: If credentials invalid
            ExchangeError: If validation fails
        """
        try:
            # Get trading service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            # Validate credentials
            result = await trading_service.validate_credentials(
                exchange=self.exchange,
                api_key=self.api_key,
                api_secret=self.api_secret,
                passphrase=self.passphrase,
                testnet=self.is_testnet
            )

            if not result["valid"]:
                raise ValidationError(
                    "Invalid API credentials",
                    context={
                        "exchange": self.exchange,
                        "errors": result["errors"]
                    }
                )

            # Reset error tracking
            if self.error_count > 0:
                self.error_count = 0
                self.last_error = None
                self.is_active = True
                await self.save()

            logger.info(
                "Validated API credentials",
                extra={
                    "account_id": str(self.id),
                    "exchange": self.exchange
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError(
                "Credential validation failed",
                context={
                    "account_id": str(self.id),
                    "exchange": self.exchange,
                    "error": str(e)
                }
            )

    async def sync_balance(self) -> None:
        """
        Sync account balance and positions.
        
        Raises:
            ExchangeError: If sync fails
        """
        try:
            # Get trading service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            # Get balance info
            balance_info = await trading_service.get_account_balance(
                account_id=str(self.id)
            )

            # Get positions
            positions = await trading_service.get_account_positions(
                account_id=str(self.id)
            )

            # Update account state
            old_balance = self.current_balance
            old_equity = self.current_equity
            
            self.current_balance = Decimal(str(balance_info["balance"]))
            self.current_equity = Decimal(str(balance_info["equity"]))
            self.position_value = sum(
                Decimal(str(p["notional_value"])) 
                for p in positions
            )
            self.open_positions = len(positions)
            self.last_sync = datetime.utcnow()
            self.modified_at = datetime.utcnow()
            
            # Reset error tracking
            self.error_count = 0
            self.last_error = None

            await self.save()

            # Log significant changes
            if abs(self.current_balance - old_balance) > Decimal("0.01"):
                logger.info(
                    "Significant balance change",
                    extra={
                        "account_id": str(self.id),
                        "change": str(self.current_balance - old_balance)
                    }
                )

            # Update performance metrics
            metrics = {
                "balance": self.current_balance,
                "equity": self.current_equity,
                "positions": self.open_positions,
                "total_positions": self.total_positions,
                "successful_positions": self.successful_positions,
                "position_value": self.position_value,
                "trading_fees": self.trading_fees,
                "funding_fees": self.funding_fees
            }

            performance_service = await reference_manager.get_service(
                service_type="PerformanceService"
            )
            
            await performance_service.update_daily_performance(
                account_id=str(self.id),
                date=datetime.utcnow(),
                metrics=metrics
            )

        except Exception as e:
            # Track error state
            self.error_count += 1
            self.last_error = str(e)
            
            if self.error_count >= trading_constants["MAX_SYNC_ERRORS"]:
                self.is_active = False
                logger.warning(
                    "Account deactivated due to errors",
                    extra={
                        "account_id": str(self.id),
                        "error_count": self.error_count
                    }
                )
                
            await self.save()

            raise ExchangeError(
                "Balance sync failed",
                context={
                    "account_id": str(self.id),
                    "exchange": self.exchange, 
                    "error_count": self.error_count,
                    "error": str(e)
                }
            )

    async def check_trade_limits(self) -> Dict[str, bool]:
        """
        Check if account can trade.
        
        Returns:
            Dict: Trade limit results
            
        Raises:
            ValidationError: If limit check fails
        """ 
        try:
            drawdown = self._calculate_drawdown()
            
            # Get minimum balance
            min_balance = trading_constants.get(
                "MIN_ACCOUNT_BALANCE",
                self.current_balance * Decimal("0.1")  # 10% default
            )
            
            limits = {
                "is_active": self.is_active,
                "max_positions": self.open_positions < self.max_positions,
                "max_drawdown": drawdown < self.max_drawdown,
                "min_balance": self.current_balance >= min_balance,
                "error_threshold": self.error_count < trading_constants["MAX_SYNC_ERRORS"]
            }
            
            limits["can_trade"] = all(limits.values())

            # Log restrictions
            if not limits["can_trade"]:
                failed = [k for k, v in limits.items() if not v]
                logger.warning(
                    "Account trading restricted",
                    extra={
                        "account_id": str(self.id),
                        "failed_checks": failed
                    }
                )

            return limits
            
        except Exception as e:
            raise ValidationError(
                "Trade limit check failed",
                context={
                    "account_id": str(self.id),
                    "error": str(e)
                }
            )

    def _calculate_drawdown(self) -> float:
        """
        Calculate current drawdown percentage.
        
        Returns:
            float: Current drawdown
        """
        try:
            if self.initial_balance <= 0:
                return 0

            drawdown = ((self.initial_balance - self.current_equity) / 
                       self.initial_balance * 100)

            # Log high drawdown
            if drawdown > self.max_drawdown * 0.8:
                logger.warning(
                    "High drawdown detected",
                    extra={
                        "account_id": str(self.id),
                        "drawdown": drawdown,
                        "max_drawdown": self.max_drawdown 
                    }
                )

            return max(0, drawdown)

        except Exception as e:
            logger.error(
                "Drawdown calculation failed",
                extra={
                    "account_id": str(self.id),
                    "error": str(e)
                }
            )
            return 0

    async def add_to_group(self, group_id: str) -> None:
        """
        Add account to group.
        
        Args:
            group_id: Group to add to
            
        Raises:
            ValidationError: If group invalid
            DatabaseError: If update fails
        """
        try:
            if group_id in self.group_ids:
                raise ValidationError(
                    "Account already in group",
                    context={
                        "group_id": group_id,
                        "account_id": str(self.id)
                    }
                )

            # Validate group exists
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

            # Add to group
            self.group_ids.append(group_id)
            self.modified_at = datetime.utcnow()
            await self.save()

            # Update reference
            await reference_manager.add_reference(
                source_type="Account",
                target_type="Group",
                source_id=str(self.id),
                target_id=group_id
            )

            logger.info(
                "Added account to group",
                extra={
                    "account_id": str(self.id),
                    "group_id": group_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to add to group",
                context={
                    "account_id": str(self.id),
                    "group_id": group_id,
                    "error": str(e)
                }
            )

    async def remove_from_group(self, group_id: str) -> None:
        """
        Remove account from group.
        
        Args:
            group_id: Group to remove from
            
        Raises:
            ValidationError: If not in group
            DatabaseError: If removal fails
        """
        try:
            if group_id not in self.group_ids:
                raise ValidationError(
                    "Account not in group",
                    context={
                        "group_id": group_id,
                        "account_id": str(self.id)
                    }
                )

            # Remove from group
            self.group_ids.remove(group_id)
            self.modified_at = datetime.utcnow()
            await self.save()

            # Remove reference
            await reference_manager.remove_reference(
                source_type="Account",
                target_type="Group",
                source_id=str(self.id),
                target_id=group_id
            )

            logger.info(
                "Removed account from group",
                extra={
                    "account_id": str(self.id),
                    "group_id": group_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to remove from group",
                context={
                    "account_id": str(self.id),
                    "group_id": group_id,
                    "error": str(e)
                }
            )

    async def get_performance_metrics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get account performance metrics.
        
        Args:
            start_date: Optional start date
            end_date: Optional end date
            
        Returns:
            Dict: Performance metrics
            
        Raises:
            DatabaseError: If metrics retrieval fails
        """
        try:
            # Default to last 24 hours
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=1)
            if not end_date:
                end_date = datetime.utcnow()

            # Get performance service
            performance_service = await reference_manager.get_service(
                service_type="PerformanceService"
            )

            metrics = await performance_service.get_account_metrics(
                account_id=str(self.id),
                start_date=start_date,
                end_date=end_date
            )

            return {
                **metrics,
                "account_id": str(self.id),
                "exchange": self.exchange.value,
                "current_balance": float(self.current_balance),
                "current_equity": float(self.current_equity)
            }

        except Exception as e:
            raise DatabaseError(
                "Failed to get performance metrics",
                context={
                    "account_id": str(self.id),
                    "date_range": f"{start_date} to {end_date}",
                    "error": str(e)
                }
            )

    async def record_trade(
        self,
        symbol: str,
        side: str,
        size: str,
        entry_price: str,
        source: TradeSource
    ) -> None:
        """
        Record executed trade with validation.
        
        Args:
            symbol: Trading symbol
            side: Order side 
            size: Position size
            entry_price: Entry price
            source: Trade source
            
        Raises:
            ValidationError: If trade data invalid
            DatabaseError: If recording fails
        """
        try:
            # Get trade service
            trade_service = await reference_manager.get_service(
                service_type="TradeService"
            )
            
            # Record trade
            trade_result = await trade_service.record_trade(
                account_id=str(self.id),
                symbol=symbol,
                side=side,
                size=size,
                entry_price=entry_price,
                source=source
            )

            # Update position count
            self.total_positions += 1
            if trade_result.get("profitable", False):
                self.successful_positions += 1
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Recorded trade execution",
                extra={
                    "account_id": str(self.id),
                    "symbol": symbol,
                    "side": side,
                    "source": source.value
                }
            )

        except Exception as e:
            raise DatabaseError(
                "Failed to record trade",
                context={
                    "account_id": str(self.id),
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def get_trade_history(
        self,
        start_date: datetime,
        end_date: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get account trade history.
        
        Args:
            start_date: Start date
            end_date: End date
            symbol: Optional symbol filter
            
        Returns:
            List[Dict]: Trade history
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            # Get trade service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            trades = await trading_service.get_trade_history(
                account_id=str(self.id),
                start_date=start_date,
                end_date=end_date,
                symbol=symbol
            )

            return trades

        except Exception as e:
            raise DatabaseError(
                "Failed to get trade history",
                context={
                    "account_id": str(self.id),
                    "date_range": f"{start_date} to {end_date}",
                    "symbol": symbol,
                    "error": str(e)
                }
            )

    async def get_position_history(
        self,
        start_date: datetime,
        end_date: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get account position history.
        
        Args:
            start_date: Start date
            end_date: End date
            symbol: Optional symbol filter
            
        Returns:
            List[Dict]: Position history
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            # Get trading service
            trading_service = await reference_manager.get_service(
                service_type="TradingService"
            )

            positions = await trading_service.get_position_history(
                account_id=str(self.id),
                start_date=start_date,
                end_date=end_date,
                symbol=symbol
            )

            return positions

        except Exception as e:
            raise DatabaseError(
                "Failed to get position history",
                context={
                    "account_id": str(self.id),
                    "date_range": f"{start_date} to {end_date}",
                    "symbol": symbol,
                    "error": str(e)
                }
            )

    def to_dict(self) -> ModelState:
        """Convert account to dictionary format."""
        return {
            "account_info": {
                "id": str(self.id),
                "user_id": self.user_id,
                "name": self.name,
                "exchange": self.exchange.value,
                "is_testnet": self.is_testnet,
                "is_active": self.is_active
            },
            "relationships": {
                "bot_id": self.bot_id,
                "group_ids": self.group_ids
            },
            "balances": {
                "initial": str(self.initial_balance),
                "current": str(self.current_balance),
                "equity": str(self.current_equity)
            },
            "positions": {
                "open": self.open_positions,
                "total": self.total_positions,
                "successful": self.successful_positions,
                "value": str(self.position_value)
            },
            "fees": {
                "trading": str(self.trading_fees),
                "funding": str(self.funding_fees)
            },
            "settings": {
                "max_positions": self.max_positions,
                "max_drawdown": self.max_drawdown
            },
            "timestamps": {
                "created_at": self.created_at.isoformat(),
                "modified_at": self.modified_at.isoformat() if self.modified_at else None,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None
            },
            "error_info": {
                "error_count": self.error_count,
                "last_error": self.last_error
            }
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"Account(exchange={self.exchange}, "
            f"balance={float(self.current_balance):.2f}, "
            f"positions={self.open_positions})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    ValidationError,
    DatabaseError,
    ExchangeError
)
from app.core.logging.logger import get_logger
from app.core.references import (
    ExchangeType,
    ModelState,
    TradeSource
)
from app.core.config.constants import trading_constants
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)