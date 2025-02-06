"""
Bot model with enhanced error handling and service integration.

Features:
- Signal routing to exchange operations
- WebSocket connection management
- Performance tracking
- Enhanced error handling
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator


class Bot(Document):
    """
    Bot model for managing signal routing and account connections.
    
    Features:
    - Signal routing
    - Connection management
    - Performance tracking
    - Error monitoring
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
    
    # Status tracking
    status: BotStatus = Field(
        BotStatus.STOPPED,
        description="Current operational status"
    )
    connected_accounts: List[str] = Field(
        default_factory=list,
        description="Connected account IDs"
    )
    
    # Performance tracking
    total_signals: int = Field(
        0,
        description="Total signals processed"
    )
    successful_signals: int = Field(
        0, 
        description="Successfully executed signals"
    )
    failed_signals: int = Field(
        0,
        description="Failed signal executions"
    )
    total_positions: int = Field(
        0,
        description="Total positions taken"
    )
    successful_positions: int = Field(
        0,
        description="Number of profitable positions"
    )
    
    # WebSocket state
    ws_connected: bool = Field(
        False,
        description="WebSocket connection status"
    )
    subscribed_accounts: List[str] = Field(
        default_factory=list,
        description="Accounts with active subscriptions"
    )
    
    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.utcnow,  
        description="Bot creation timestamp"
    )
    modified_at: Optional[datetime] = Field(
        None,
        description="Last modification timestamp" 
    )
    last_signal: Optional[datetime] = Field(
        None, 
        description="Last signal timestamp"
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )
    error_count: int = Field(
        0,
        description="Consecutive error count"
    )

    class Settings:
        """Collection settings and indexes."""
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
        """
        Validate bot name follows format.
        
        Args:
            v: Bot name to validate
            info: Validation context
            
        Returns:
            str: Validated name
            
        Raises:
            ValidationError: If name invalid
        """
        try:
            # Check for empty name
            if not v or not v.strip():
                raise ValidationError(
                    "Bot name cannot be empty",
                    context={"name": v}
                )

            name = v.strip()
            base_name = info.data.get("base_name")
            timeframe = info.data.get("timeframe")

            if base_name and timeframe:
                expected = f"{base_name}-{timeframe}"
                if name != expected:
                    raise ValidationError(
                        "Invalid bot name format",
                        context={
                            "name": name,
                            "expected": expected,
                            "base_name": base_name,
                            "timeframe": timeframe
                        }
                    )

            return name

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Bot name validation failed",
                context={
                    "name": v,
                    "error": str(e)
                }
            )

    @before_event([Replace, Insert]) 
    async def validate_references(self):
        """
        Validate model references and state.
        
        Validates:
        - Account references exist and are active
        - No duplicate references
        - Connection states
        - WebSocket health
        
        Raises:
            ValidationError: If validation fails
            DatabaseError: If lookups fail
        """
        try:
            seen_accounts = set()
            
            for account_id in self.connected_accounts:
                # Check duplicates
                if account_id in seen_accounts:
                    raise ValidationError(
                        "Duplicate account reference",
                        context={"account_id": account_id}
                    )
                seen_accounts.add(account_id)

                # Validate reference
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

                # Check WebSocket state if active
                try:
                    if self.status == BotStatus.ACTIVE:
                        ws_status = await ws_manager.get_connection_status(account_id)
                        if not ws_status.get("connected", False):
                            raise ValidationError(
                                "WebSocket not connected for active bot",
                                context={
                                    "account_id": account_id,
                                    "ws_status": ws_status
                                }
                            )
                except Exception as e:
                    logger.warning(
                        "Failed to verify WebSocket",
                        extra={
                            "account_id": account_id,
                            "error": str(e)
                        }
                    )

            self.modified_at = datetime.utcnow()

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "bot_id": str(self.id),
                    "error": str(e)
                }
            )

    async def connect_account(self, account_id: str) -> None:
        """
        Connect account with validation.
        
        Args:
            account_id: Account to connect
            
        Raises:
            ValidationError: If connection invalid
            DatabaseError: If connection fails
        """
        try:
            # Add reference first
            await reference_manager.add_reference(
                source_type="Bot",
                target_type="Account",
                source_id=str(self.id),
                target_id=account_id
            )

            # Initialize WebSocket if active
            if self.status == BotStatus.ACTIVE:
                await ws_manager.create_connection(account_id)
                
                # Subscribe to channels
                channels = ["positions", "orders", "balances"]
                for channel in channels:
                    await ws_manager.subscribe(account_id, channel)

            # Update bot state
            if account_id not in self.connected_accounts:
                self.connected_accounts.append(account_id)
                if self.status == BotStatus.ACTIVE:
                    self.subscribed_accounts.append(account_id)
                self.modified_at = datetime.utcnow()
                await self.save()

            logger.info(
                "Connected account to bot",
                extra={
                    "bot_id": str(self.id),
                    "account_id": account_id,
                    "status": self.status
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to connect account",
                context={
                    "bot_id": str(self.id),
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def disconnect_account(self, account_id: str) -> None:
        """
        Disconnect account with cleanup.
        
        Args:
            account_id: Account to disconnect
            
        Raises:
            ValidationError: If not connected
            DatabaseError: If disconnection fails
        """
        try:
            # Remove reference
            await reference_manager.remove_reference(
                source_type="Bot",
                target_type="Account", 
                source_id=str(self.id),
                target_id=account_id
            )

            # Close WebSocket
            if account_id in self.subscribed_accounts:
                await ws_manager.close_connection(account_id)

            # Update state
            if account_id in self.connected_accounts:
                self.connected_accounts.remove(account_id)
            if account_id in self.subscribed_accounts:
                self.subscribed_accounts.remove(account_id)
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Disconnected account from bot",
                extra={
                    "bot_id": str(self.id),
                    "account_id": account_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to disconnect account",
                context={
                    "bot_id": str(self.id),
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def process_signal(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route signal to connected accounts via exchange operations.
        
        Args:
            signal_data: Signal parameters
            
        Returns:
            Dict: Signal execution results
            
        Raises:
            ValidationError: If bot not active
            DatabaseError: If processing fails
        """
        try:
            if self.status != BotStatus.ACTIVE:
                raise ValidationError(
                    "Bot not active",
                    context={
                        "bot_id": str(self.id),
                        "status": self.status
                    }
                )

            results = []
            success_count = 0
            error_count = 0

            # Route to connected accounts
            for account_id in self.connected_accounts:
                try:
                    # Get exchange operations instance
                    operations = await exchange_factory.get_instance(
                        account_id,
                        reference_manager  
                    )

                    result = await operations.execute_trade(
                        account_id=account_id,
                        symbol=signal_data["symbol"],
                        side=signal_data["side"],
                        order_type=signal_data["order_type"],
                        size=signal_data["size"],
                        leverage=signal_data["leverage"],
                        take_profit=signal_data.get("take_profit"),
                        source=TradeSource.BOT
                    )

                    results.append({
                        "account_id": account_id,
                        "success": result["success"],
                        "details": result
                    })

                    if result["success"]:
                        success_count += 1
                    else:
                        error_count += 1

                except Exception as e:
                    error_count += 1
                    results.append({
                        "account_id": account_id,
                        "success": False,
                        "error": str(e)
                    })

            # Update metrics
            self.total_signals += 1
            self.successful_signals += success_count
            self.failed_signals += error_count
            self.last_signal = datetime.utcnow()
            self.modified_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Processed signal",
                extra={
                    "bot_id": str(self.id),
                    "success_count": success_count,
                    "error_count": error_count
                }
            )

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
            raise DatabaseError(
                "Signal processing failed",
                context={
                    "bot_id": str(self.id),
                    "signal": signal_data,
                    "error": str(e)
                }
            )

    async def update_status(self, new_status: BotStatus) -> None:
        """
        Update bot status with WebSocket management.
        
        Args:
            new_status: New status to set
            
        Raises:
            ValidationError: If status transition invalid
            DatabaseError: If update fails
        """
        try:
            # Validate transition
            valid_transitions = {
                BotStatus.STOPPED: [BotStatus.ACTIVE],
                BotStatus.ACTIVE: [BotStatus.PAUSED, BotStatus.STOPPED],
                BotStatus.PAUSED: [BotStatus.ACTIVE, BotStatus.STOPPED]
            }

            if new_status not in valid_transitions.get(self.status, []):
                raise ValidationError(
                    "Invalid status transition",
                    context={
                        "current": self.status,
                        "attempted": new_status,
                        "valid_transitions": valid_transitions.get(self.status, [])
                    }
                )

            old_status = self.status
            self.status = new_status
            self.modified_at = datetime.utcnow()

            # Handle WebSocket connections
            if new_status == BotStatus.ACTIVE:
                for account_id in self.connected_accounts:
                    if account_id not in self.subscribed_accounts:
                        await self._setup_account_websocket(account_id)
            elif new_status == BotStatus.STOPPED:
                for account_id in self.subscribed_accounts[:]:
                    await ws_manager.close_connection(account_id)
                    self.subscribed_accounts.remove(account_id)

            await self.save()

            # Notify change
            await telegram_bot.notify_bot_status(str(self.id), new_status)

            logger.info(
                "Updated bot status",
                extra={
                    "bot_id": str(self.id),
                    "old_status": old_status,
                    "new_status": new_status
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update status",
                context={
                    "bot_id": str(self.id),
                    "current": self.status,
                    "new_status": new_status,
                    "error": str(e)
                }
            )

    async def _setup_account_websocket(self, account_id: str) -> None:
        """
        Setup WebSocket connection for account.
        
        Args:
            account_id: Account to setup
            
        Raises:
            ValidationError: If account invalid
            WebSocketError: If setup fails
        """
        try:
            # Get account config
            account = await reference_manager.get_reference(account_id)
            if not account:
                raise ValidationError(
                    "Account not found",
                    context={"account_id": account_id}
                )

            # Get operations instance for config
            operations = await exchange_factory.get_instance(
                account_id,
                reference_manager
            )

            # Create connection
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
            channels = ["positions", "orders", "balances"]
            for channel in channels:
                await ws_manager.subscribe(account_id, channel)

            if account_id not in self.subscribed_accounts:
                self.subscribed_accounts.append(account_id)
                await self.save()

            logger.info(
                "Setup WebSocket connection",
                extra={
                    "bot_id": str(self.id),
                    "account_id": account_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise WebSocketError(
                "WebSocket setup failed",
                context={
                    "bot_id": str(self.id),
                    "account_id": account_id,
                    "error": str(e)
                }
            )

    async def get_status(self) -> Dict[str, Any]:
        """
        Get detailed bot status.
        
        Returns:
            Dict: Current status information
            
        Raises:
            DatabaseError: If status check fails
        """
        try:
            # Get account statuses
            account_status = {}
            for account_id in self.connected_accounts:
                try:
                    operations = await exchange_factory.get_instance(
                        account_id,
                        reference_manager
                    )
                    
                    # Get positions and balance
                    positions = await operations.get_all_positions()
                    balance = await operations.get_balance()
                    
                    # Get WebSocket status
                    ws_status = await ws_manager.get_connection_status(account_id)
                    
                    account_status[account_id] = {
                        "connected": ws_status.get("connected", False),
                        "positions": len(positions),
                        "balance": str(balance["balance"]),
                        "equity": str(balance["equity"]),
                        "websocket": ws_status
                    }

                except Exception as e:
                    account_status[account_id] = {
                        "error": str(e),
                        "connected": False
                    }

            # Calculate success rate
            success_rate = (
                (self.successful_signals / self.total_signals * 100)
                if self.total_signals > 0 else 0
            )

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
                    "success_rate": success_rate,
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
            raise DatabaseError(
                "Failed to get bot status",
                context={
                    "bot_id": str(self.id),
                    "error": str(e)
                }
            )

    def to_dict(self) -> ModelState:
        """Convert to dictionary format."""
        return {
            "bot_info": {
                "id": str(self.id),
                "name": self.name,
                "base_name": self.base_name,
                "timeframe": self.timeframe,
                "status": self.status
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
        """String representation."""
        return (
            f"Bot(name='{self.name}', "
            f"status={self.status}, "
            f"accounts={len(self.connected_accounts)})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    ValidationError,
    DatabaseError,
    WebSocketError
)
from app.core.logging.logger import get_logger
from app.core.references import (
    BotStatus,
    TimeFrame,
    ModelState,
    TradeSource
)
from app.services.exchange.factory import exchange_factory
from app.services.websocket.manager import ws_manager
from app.services.reference.manager import reference_manager
from app.services.telegram.service import telegram_bot

logger = get_logger(__name__)