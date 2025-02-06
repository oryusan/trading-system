"""
Exchange operations service with protocol-based dependency management.

Features:
- High-level trading operations
- Resource management
- WebSocket integration
- Error handling
"""

from typing import Dict, Optional, Any, Protocol
from decimal import Decimal
import asyncio
from datetime import datetime

class ExchangeOperations:
    """
    High-level exchange operations service.
    
    Features:
    - Trading operations
    - Position management
    - Performance tracking
    - WebSocket integration
    """
    
    def __init__(
        self,
        account_id: str,
        exchange_factory: ExchangeFactoryProtocol,
        reference_manager: ReferenceManagerProtocol,
        ws_manager: WebSocketManagerProtocol,
        performance_service: PerformanceServiceProtocol
    ):
        """
        Initialize with dependencies.
        
        Args:
            account_id: Account identifier
            exchange_factory: Factory for exchange instances
            reference_manager: Reference manager
            ws_manager: WebSocket manager
            performance_service: Performance tracking
        """
        self.account_id = account_id
        self.exchange_factory = exchange_factory
        self.reference_manager = reference_manager
        self.ws_manager = ws_manager
        self.performance_service = performance_service
        self._exchange: Optional[ExchangeProtocol] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self.logger = logger.getChild(f"exchange_ops_{account_id}")

    async def initialize(self) -> None:
        """
        Initialize operations and validate setup.
        
        Raises:
            ConfigurationError: If account not found
            ValidationError: If validation fails
            ExchangeError: If setup fails
        """
        if self._initialized:
            return
            
        async with self._lock:
            try:
                # Get account details
                self.account = await self.reference_manager.get_reference(
                    self.account_id
                )
                if not self.account:
                    raise ConfigurationError(
                        "Account not found",
                        context={"account_id": self.account_id}
                    )

                # Get exchange instance
                self._exchange = await self.exchange_factory.get_instance(
                    self.account_id,
                    self.reference_manager
                )

                # Initialize WebSocket if needed
                if self.account.get("websocket_enabled"):
                    await self.ws_manager.create_connection(
                        self.account_id,
                        self.account["exchange"]
                    )

                self._initialized = True
                
                self.logger.info(
                    "Initialized exchange operations",
                    extra={
                        "account_id": self.account_id,
                        "exchange": self.account["exchange"]
                    }
                )

            except Exception as e:
                self._initialized = False
                raise ExchangeError(
                    "Failed to initialize operations",
                    context={
                        "account_id": self.account_id,
                        "error": str(e)
                    }
                )

    async def _ensure_initialized(self) -> None:
        """Ensure operations are initialized."""
        if not self._initialized:
            await self.initialize()

    async def execute_trade(
        self,
        symbol: str,
        side: str,
        order_type: OrderType,
        risk_percentage: Decimal,
        leverage: int,
        take_profit: Optional[Decimal] = None,
        source: TradeSource = TradeSource.TRADING_PANEL
    ) -> Dict[str, Any]:
        """
        Execute trade with validation and monitoring.
        
        Uses the same core trade execution logic as signal and ladder trades
        to maintain consistency across the trading system.
        
        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Type of order
            risk_percentage: Risk size
            leverage: Position leverage
            take_profit: Optional take profit
            source: Trade source
            
        Returns:
            Dict with order results
            
        Raises:
            ValidationError: If parameters invalid
            ExchangeError: If execution fails
        """
        await self._ensure_initialized()
        
        try:
            # Get current balance
            balance = await self._exchange.get_balance()
            current_balance = balance["balance"]

            # Calculate position size
            size = await self._calc_position_size(
                symbol=symbol,
                risk=str(risk_percentage),
                leverage=str(leverage),
                balance=current_balance
            )

            # Handle existing position
            position_result = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=str(leverage)
            )
            
            if position_result.get("action_needed"):
                raise ExchangeError(
                    "Failed to handle position",
                    context={
                        "symbol": symbol,
                        "result": position_result
                    }
                )

            # Get current price for order
            prices = await self._exchange.get_current_price(symbol)
            entry_price = prices["bid_price" if side.lower() == "buy" else "ask_price"]

            # Prepare order parameters
            order_params = {
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "size": size,
                "price": str(entry_price),
                "leverage": str(leverage)
            }

            # Add take profit if specified
            if take_profit:
                validated_tp = await self._validate_price(
                    symbol=symbol,
                    side=side,
                    price_type="take_profit",
                    price=take_profit
                )
                order_params["take_profit"] = str(validated_tp)

            # Execute order
            order_result = await self._execute_order(order_params)

            # Record trade
            await self._record_trade(
                symbol=symbol,
                side=side,
                size=size,
                order_result=order_result,
                source=source
            )

            # Update performance
            await self._update_performance()

            self.logger.info(
                "Trade executed successfully",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "source": source.value
                }
            )

            return {
                "success": True,
                "order": order_result,
                "position": position_result
            }

        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            raise ExchangeError(
                "Trade execution failed",
                context={
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def handle_current_position(
        self,
        symbol: str,
        side: str,
        leverage: str
    ) -> Dict[str, Any]:
        """
        Handle existing position before new trade.
        
        Args:
            symbol: Trading symbol
            side: Desired position side
            leverage: Desired leverage
            
        Returns:
            Dict with position status
            
        Raises:
            ExchangeError: If handling fails
        """
        await self._ensure_initialized()

        try:
            # Get current position
            position = await self._exchange.get_position(symbol)

            # No position or empty
            if not position or position.get("size", "0") == "0":
                await self._exchange.set_leverage(symbol, leverage)
                return {
                    "status": "initialized",
                    "action_needed": False
                }

            # Check opposite side
            current_side = position.get("side", "").lower()
            if current_side and current_side != side.lower():
                # Cancel orders
                await self._exchange.cancel_all_orders(symbol)
                
                # Close position
                await self._exchange.close_position(symbol)
                
                # Set leverage
                await self._exchange.set_leverage(symbol, leverage)

                return {
                    "status": "closed",
                    "action_needed": False
                }

            # Same side
            return {
                "status": "compatible",
                "action_needed": False,
                "current_position": position
            }

        except Exception as e:
            raise ExchangeError(
                "Failed to handle position",
                context={
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def position_control(
        self,
        symbol: str,
        control_type: str
    ) -> Dict[str, Any]:
        """
        Manage existing position.
        
        Args:
            symbol: Trading symbol
            control_type: Control action
            
        Returns:
            Dict with control results
            
        Raises:
            ExchangeError: If control fails
        """
        await self._ensure_initialized()

        try:
            # Cancel all orders
            cancel_result = await self._exchange.cancel_all_orders(symbol)
            if not cancel_result.get("success"):
                raise ExchangeError(
                    "Failed to cancel orders",
                    context={
                        "symbol": symbol,
                        "result": cancel_result
                    }
                )

            # Close position
            close_result = await self._exchange.close_position(symbol)
            if not close_result.get("success"):
                raise ExchangeError(
                    "Failed to close position",
                    context={
                        "symbol": symbol,
                        "result": close_result
                    }
                )

            # Reset mode
            await self._exchange.set_position_mode()

            self.logger.info(
                "Position control executed",
                extra={
                    "symbol": symbol,
                    "type": control_type
                }
            )

            return {
                "success": True,
                "cancel_result": cancel_result,
                "close_result": close_result
            }

        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(
                "Position control failed",
                context={
                    "symbol": symbol,
                    "type": control_type,
                    "error": str(e)
                }
            )

    async def place_signal(
        self,
        symbol: str,
        side: str,
        size: str,
        client_id: str,
        leverage: str,
        take_profit: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place signal trade.
        
        Args:
            symbol: Trading symbol
            side: Order side
            size: Order size
            client_id: Client order ID
            leverage: Position leverage
            take_profit: Optional take profit
            
        Returns:
            Dict with trade results
            
        Raises:
            ValidationError: If parameters invalid
            ExchangeError: If trade fails
        """
        await self._ensure_initialized()

        try:
            # Handle existing position
            position_result = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            
            if position_result.get("action_needed"):
                raise ExchangeError(
                    "Failed to handle position",
                    context={
                        "symbol": symbol,
                        "result": position_result
                    }
                )

            # Get entry price
            prices = await self._exchange.get_current_price(symbol)
            entry_price = prices["bid_price" if side.lower() == "buy" else "ask_price"]

            # Prepare order
            order_params = {
                "symbol": symbol,
                "side": side,
                "order_type": "limit",
                "size": size,
                "price": str(entry_price),
                "client_id": client_id,
                "leverage": leverage
            }

            # Add take profit if specified
            if take_profit:
                validated_tp = await self._validate_price(
                    symbol=symbol,
                    side=side,
                    price_type="take_profit",
                    price=Decimal(take_profit)
                )
                order_params["take_profit"] = str(validated_tp)

            # Place order
            order_result = await self._execute_order(order_params)

            self.logger.info(
                "Signal order placed",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "size": size
                }
            )

            return {
                "success": True,
                "order": order_result,
                "position": position_result
            }

        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            raise ExchangeError(
                "Failed to place signal",
                context={
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def place_ladder(
        self,
        symbol: str,
        side: str,
        size: str,
        client_id: str,
        take_profit: str,
        leverage: str
    ) -> Dict[str, Any]:
        """
        Place ladder trade.
        
        Args:
            symbol: Trading symbol
            side: Order side
            size: Order size
            client_id: Client order ID
            take_profit: Take profit price
            leverage: Position leverage
            
        Returns:
            Dict with trade results
            
        Raises:
            ValidationError: If parameters invalid
            ExchangeError: If trade fails
        """
        await self._ensure_initialized()

        try:
            # Handle current position
            position_result = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            
            if position_result.get("action_needed"):
                raise ExchangeError(
                    "Failed to handle position",
                    context={
                        "symbol": symbol,
                        "result": position_result
                    }
                )

            # Cancel existing orders
            await self._exchange.cancel_all_orders(symbol)

            # Get entry price
            prices = await self._exchange.get_current_price(symbol)
            entry_price = prices["bid_price" if side.lower() == "buy" else "ask_price"]

            # Validate take profit
            validated_tp = await self._validate_price(
                symbol=symbol,
                side=side,
                price_type="take_profit",
                price=Decimal(take_profit)
            )

            # Place order
            order_params = {
                "symbol": symbol,
                "side": side,
                "order_type": "limit",
                "size": size,
                "price": str(entry_price),
                "client_id": client_id,
                "leverage": leverage,
                "take_profit": str(validated_tp)
            }

            order_result = await self._execute_order(order_params)

            self.logger.info(
                "Ladder order placed",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "size": size
                }
            )

            return {
                "success": True,
                "order": order_result,
                "position": position_result
            }

        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            raise ExchangeError(
                "Failed to place ladder",
                context={
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def _validate_price(
        self,
        symbol: str,
        side: str,
        price_type: str,
        price: Decimal
    ) -> Decimal:
        """
        Validate and normalize price.
        
        Args:
            symbol: Trading symbol
            side: Order side
            price_type: Type of price check
            price: Price to validate
            
        Returns:
            Decimal: Validated price
            
        Raises:
            ValidationError: If price invalid
        """
        try:
            if price <= 0:
                raise ValidationError(
                    "Price must be positive",
                    context={
                        "price": str(price),
                        "symbol": symbol,
                        "side": side
                    }
                )

            specs = await symbol_validator.validate_symbol(
                symbol=symbol,
                exchange_type=self.account["exchange"]
            )
            
            # Normalize to tick size
            tick_size = Decimal(specs["specifications"]["tick_size"])
            normalized = Decimal(str(round(price / tick_size))) * tick_size
            
            self.logger.debug(
                "Validated price",
                extra={
                    "symbol": symbol,
                    "original": str(price),
                    "normalized": str(normalized),
                    "price_type": price_type
                }
            )
            
            return normalized

        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError(
                "Price validation failed",
                context={
                    "symbol": symbol,
                    "price": str(price),
                    "side": side,
                    "price_type": price_type,
                    "error": str(e)
                }
            )

    async def _calc_position_size(
        self,
        symbol: str,
        risk: str,
        leverage: str,
        balance: Decimal
    ) -> str:
        """
        Calculate position size based on risk parameters.
        
        Args:
            symbol: Trading symbol
            risk: Risk percentage
            leverage: Leverage value
            balance: Current balance
            
        Returns:
            str: Calculated size
            
        Raises:
            ValidationError: If parameters invalid
            ExchangeError: If calculation fails
        """
        try:
            # Validate inputs
            try:
                risk_pct = float(risk)
                leverage_val = Decimal(leverage)
                if risk_pct <= 0 or leverage_val <= 0:
                    raise ValidationError(
                        "Risk and leverage must be positive",
                        context={
                            "risk": risk,
                            "leverage": leverage
                        }
                    )
            except Exception as e:
                raise ValidationError(
                    "Invalid numeric parameters",
                    context={
                        "risk": risk,
                        "leverage": leverage,
                        "error": str(e)
                    }
                )

            if balance <= 0:
                raise ValidationError(
                    "Insufficient balance",
                    context={"balance": str(balance)}
                )

            # Get symbol specifications
            specs = await symbol_validator.validate_symbol(
                symbol=symbol,
                exchange_type=self.account["exchange"]
            )
            lot_size = Decimal(specs["specifications"]["lot_size"])
            contract_size = Decimal(specs["specifications"]["contract_size"])

            # Calculate risk amount
            risk_amount = balance * (risk_pct / Decimal("100"))
            
            # Get current price
            prices = await self._exchange.get_current_price(symbol)
            price = prices["last_price"]
            
            # Calculate size
            raw_size = (risk_amount * leverage_val) / (contract_size * price)
            
            # Round to valid lot size
            valid_size = (raw_size // lot_size) * lot_size
            if valid_size < lot_size:
                return str(lot_size)

            return str(valid_size)

        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError(
                "Position size calculation failed",
                context={
                    "symbol": symbol,
                    "risk": risk,
                    "leverage": leverage,
                    "error": str(e)
                }
            )

    async def _execute_order(
        self,
        order_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute order with monitoring.
        
        Core order execution method used by all trading functions.
        
        Args:
            order_params: Order parameters
            
        Returns:
            Dict with execution results
            
        Raises:
            ExchangeError: If execution fails
        """
        try:
            # Place order
            result = await self._exchange.place_order(order_params)
            
            # Monitor order if we have an order ID
            if result.get("order_id"):
                monitor_result = await self._monitor_order(
                    symbol=order_params["symbol"],
                    order_id=result["order_id"]
                )
                result["monitor_status"] = monitor_result

            return result

        except Exception as e:
            raise ExchangeError(
                "Order execution failed",
                context={
                    "params": order_params,
                    "error": str(e)
                }
            )

    async def _monitor_order(
        self,
        symbol: str,
        order_id: str,
        max_attempts: int = 9,
        check_interval: float = 1.0
    ) -> Dict[str, Any]:
        """
        Monitor limit order with price updates.
        
        Args:
            symbol: Trading symbol
            order_id: Order to monitor 
            max_attempts: Update attempts
            check_interval: Seconds between checks
            
        Returns:
            Dict with monitoring results
            
        Raises:
            ExchangeError: If monitoring fails
        """
        try:
            attempt = 0
            while attempt < max_attempts:
                await asyncio.sleep(check_interval)
                
                # Check order status
                order_info = await self._exchange.get_order_status(
                    symbol=symbol,
                    order_id=order_id
                )
                if not order_info:
                    return {"status": "filled"}

                # Get current price
                current_prices = await self._exchange.get_current_price(symbol)
                order_price = Decimal(order_info["price"])
                side = order_info["side"].lower()

                # Check if update needed
                price_diff_threshold = Decimal("0.001")  # 0.1%
                needs_update = False
                
                if side == "buy":
                    if current_prices["last_price"] > order_price * (1 + price_diff_threshold):
                        needs_update = True
                        new_price = current_prices["bid_price"]
                else:  # sell
                    if current_prices["last_price"] < order_price * (1 - price_diff_threshold):
                        needs_update = True
                        new_price = current_prices["ask_price"]

                if needs_update:
                    try:
                        await self._exchange.amend_order(
                            symbol=symbol,
                            order_id=order_id,
                            new_price=new_price
                        )
                    except ExchangeError as e:
                        self.logger.warning(
                            "Failed to amend order",
                            extra={
                                "symbol": symbol,
                                "order_id": order_id,
                                "attempt": attempt,
                                "error": str(e)
                            }
                        )

                attempt += 1

            return {
                "status": "timeout",
                "attempts": attempt
            }

        except Exception as e:
            raise ExchangeError(
                "Order monitoring failed",
                context={
                    "symbol": symbol,
                    "order_id": order_id,
                    "attempt": attempt,
                    "error": str(e)
                }
            )

    async def _record_trade(
        self,
        symbol: str,
        side: str,
        size: str,
        order_result: Dict[str, Any],
        source: TradeSource
    ) -> None:
        """Record executed trade."""
        try:
            await self.account.record_trade(
                symbol=symbol,
                side=side,
                size=size,
                entry_price=order_result["entry_price"],
                source=source
            )
        except Exception as e:
            self.logger.error(
                "Failed to record trade",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "error": str(e)
                }
            )

    async def _update_performance(self) -> None:
        """Update performance metrics."""
        try:
            # Get balance and positions
            balance = await self._exchange.get_balance()
            positions = await self._exchange.get_all_positions()

            # Update metrics
            metrics = {
                "balance": balance["balance"],
                "equity": balance["equity"],
                "positions": len(positions),
                "position_value": sum(
                    Decimal(str(p.get("notional_value", "0")))
                    for p in positions
                )
            }

            await self.performance_service.update_daily_performance(
                account_id=self.account_id,
                date=datetime.utcnow(),
                metrics=metrics
            )

        except Exception as e:
            self.logger.error(
                "Failed to update performance",
                extra={
                    "account_id": self.account_id,
                    "error": str(e)
                }
            )

    @classmethod
    async def terminate_bot_accounts(
        cls,
        bot_id: str,
        reference_manager: ReferenceManagerProtocol,
        exchange_factory: ExchangeFactoryProtocol
    ) -> Dict[str, Any]:
        """
        Terminate all positions for bot accounts.
        
        Args:
            bot_id: Bot to terminate
            reference_manager: Reference manager
            exchange_factory: Exchange factory
            
        Returns:
            Dict with termination results
            
        Raises:
            NotFoundError: If bot not found
            ExchangeError: If termination fails
        """
        try:
            # Validate bot exists
            valid = await reference_manager.validate_reference(
                source_type="ExchangeOperations",
                target_type="Bot",
                reference_id=bot_id
            )
            if not valid:
                raise NotFoundError(
                    "Bot not found",
                    context={"bot_id": bot_id}
                )

            # Get connected accounts
            accounts = await reference_manager.get_references(
                source_type="Bot",
                reference_id=bot_id
            )

            results = []
            for account in accounts:
                try:
                    # Get exchange instance
                    exchange = await exchange_factory.get_instance(
                        str(account.id),
                        reference_manager
                    )
                    
                    # Close all positions
                    positions = await exchange.get_all_positions()
                    for position in positions:
                        await exchange.close_position(position["symbol"])
                    
                    results.append({
                        "account_id": str(account.id),
                        "success": True,
                        "closed_positions": len(positions)
                    })

                except Exception as e:
                    results.append({
                        "account_id": str(account.id),
                        "success": False,
                        "error": str(e)
                    })
                    continue

            success = any(r["success"] for r in results)
            
            logger.info(
                "Terminated bot accounts",
                extra={
                    "bot_id": bot_id,
                    "account_count": len(results),
                    "success": success
                }
            )

            return {
                "success": success,
                "results": results,
                "terminated_accounts": len(results)
            }

        except NotFoundError:
            raise
        except Exception as e:
            raise ExchangeError(
                "Failed to terminate bot accounts",
                context={
                    "bot_id": bot_id,
                    "error": str(e)
                }
            )

    @classmethod
    async def cleanup(cls) -> None:
        """Clean up all operations resources."""
        try:
            # Close WebSocket connections
            await ws_manager.close_all()
            
            # Clear caches
            symbol_validator.invalidate_cache()
            
            logger.info("Cleaned up exchange operations")

        except Exception as e:
            logger.error(
                "Cleanup failed",
                extra={"error": str(e)}
            )

# Move imports to end to avoid circular imports
from app.core.errors import (
    ExchangeError,
    ValidationError,
    ConfigurationError,
    DatabaseError,
    NotFoundError
)
from app.core.logging.logger import get_logger
from app.core.references import (
    OrderType,
    TradeSource,
    ExchangeFactoryProtocol,
    ReferenceManagerProtocol,
    WebSocketManagerProtocol,
    ExchangeProtocol,
    PerformanceServiceProtocol
)
from app.services.websocket.manager import ws_manager
from app.services.exchange.factory import symbol_validator

logger = get_logger(__name__)