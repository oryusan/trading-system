"""
High-level Exchange Operations Service

This module implements the ExchangeOperations class, which provides methods to:
  - Initialize an exchange instance for an account.
  - Execute trades (including handling current positions, placing signal and ladder orders).
  - Monitor orders and update performance based on closed (realized) trades.
  
All public methods are decorated with the global error-handling decorator
from app/core/errors/decorators.py.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.errors.base import (
    ConfigurationError,
    ExchangeError,
    NotFoundError,
    ValidationError,
)
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger
from app.services.exchange.factory import exchange_factory, symbol_validator
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)


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
        exchange_factory: Any,
        reference_manager: Any,
        ws_manager: Any,
        performance_service: Any,
    ):
        self.account_id = account_id
        self.exchange_factory = exchange_factory
        self.reference_manager = reference_manager
        self.ws_manager = ws_manager
        self.performance_service = performance_service
        self._exchange: Optional[Any] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self.logger = get_logger(f"exchange_ops_{account_id}")

    @error_handler(
        context_extractor=lambda self: {"account_id": self.account_id},
        log_message="Failed to initialize exchange operations"
    )
    async def initialize(self) -> None:
        """Initialize operations and validate setup."""
        if self._initialized:
            return
        async with self._lock:
            self.account = await self.reference_manager.get_reference(self.account_id)
            if not self.account:
                raise ConfigurationError("Account not found", context={"account_id": self.account_id})
            self._exchange = await self.exchange_factory.get_instance(self.account_id, self.reference_manager)
            if self.account.get("websocket_enabled"):
                await self.ws_manager.create_connection(self.account_id, self.account["exchange"])
            self._initialized = True
            self.logger.info("Initialized exchange operations", extra={"account_id": self.account_id, "exchange": self.account["exchange"]})

    async def _ensure_initialized(self) -> None:
        """Ensure operations are initialized."""
        if not self._initialized:
            await self.initialize()

    @error_handler(
        context_extractor=lambda self, symbol, side, **kwargs: {"symbol": symbol, "side": side},
        log_message="Trade execution failed"
    )
    async def execute_trade(
        self,
        symbol: str,
        side: str,
        order_type: Any,
        risk_percentage: str,
        leverage: str,
        take_profit: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        source: Optional[Any] = None,
        size: Optional[str] = None  # Accept pre-calculated size
    ) -> Dict[str, Any]:
        """
        Execute a trade with validation and monitoring.

        This method:
          - Uses pre-calculated size or calculates trade size based on risk% and leverage
          - Handles any existing position
          - Retrieves the entry price
          - Places the order
          - Records the trade and updates performance
          
        Args:
            symbol: Trading symbol (e.g., "BTC")
            side: Trade direction ("buy" or "sell")
            order_type: Type of order to place
            risk_percentage: Risk percentage relative to account balance
            leverage: Position leverage
            take_profit: Optional take profit price
            stop_loss: Optional stop loss price
            source: Signal source identifier
            size: Optional pre-calculated size (if provided, risk_percentage is only used for record-keeping)
            
        Returns:
            Dictionary containing:
                - success: Boolean indicating success
                - size: Calculated trade size (quantity)
                - entry_price: Entry price
                - order_size: USD value of the position (size * entry_price)
                - order details
                - position details
        """
        await self._ensure_initialized()
        
        # Use provided size or calculate based on risk percentage
        if size is not None:
            calculated_size = Decimal(str(size))
            self.logger.debug(
                "Using pre-calculated size",
                extra={
                    "account_id": self.account_id,
                    "symbol": symbol, 
                    "size": str(calculated_size)
                }
            )
        else:
            # Get account balance
            balance = await self._exchange.get_balance()
            current_balance = balance["balance"]
            
            # Calculate trade size (quantity) based on risk and leverage
            calculated_size = await self._calc_trade_size(
                symbol=symbol,
                risk_percentage=risk_percentage,
                leverage=leverage,
                balance=Decimal(str(current_balance))
            )
        
        # Handle existing position if any
        position_result = await self.handle_current_position(symbol, side, leverage)
        self._check_position_result(symbol, position_result)
        
        # Get entry price based on trade side
        entry_price = await self._get_entry_price(symbol, side)
        
        # Calculate position size in USD
        order_size = calculated_size * entry_price
        
        # Prepare order parameters
        order_params = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "size": calculated_size,
            "price": str(entry_price),
            "leverage": leverage,
        }
        
        # Add take profit and stop loss if provided
        if take_profit:
            validated_tp = await self._validate_price(symbol, side, "take_profit", take_profit)
            order_params["take_profit"] = str(validated_tp)
        
        if stop_loss:
            validated_sl = await self._validate_price(symbol, side, "stop_loss", stop_loss)
            order_params["stop_loss"] = str(validated_sl)
        
        # Execute the order
        order_result = await self._execute_order(order_params)
        
        # Record the trade in our system
        await self._record_trade(symbol, side, str(calculated_size), order_result, source)
        
        # Update performance metrics
        await self._update_performance()
        
        self.logger.info("Trade executed successfully", extra={
            "symbol": symbol, 
            "side": side, 
            "size": str(calculated_size),
            "order_size": str(order_size),
            "source": source
        })
        
        # Return comprehensive result
        return {
            "success": True,
            "order": order_result,
            "position": position_result,
            "size": str(calculated_size),
            "entry_price": str(entry_price),
            "order_size": str(order_size)
        }

    @error_handler(
        context_extractor=lambda self, symbol, side, leverage: {"symbol": symbol, "side": side, "leverage": leverage},
        log_message="Failed to handle current position"
    )
    async def handle_current_position(self, symbol: str, side: str, leverage: str) -> Dict[str, Any]:
        """
        Handle an existing position before executing a new trade.

        If no position exists, it sets the desired leverage.
        If an existing position exists with an opposing side, it cancels orders, closes the position, and resets leverage.
        If an existing position exists with the same side, it returns the position details (adding to position).
        
        Returns:
            Dictionary with position status and details
        """
        await self._ensure_initialized()
        position = await self._exchange.get_position(symbol)
        
        # No existing position - initialize leverage
        if not position or position.get("size", "0") == "0":
            await self._exchange.set_leverage(symbol, leverage)
            return {"status": "initialized", "action_needed": False}
        
        # Existing position with different side - close it
        current_side = position.get("side", "").lower()
        if current_side and current_side != side.lower():
            await self._exchange.cancel_all_orders(symbol)
            await self._exchange.close_position(symbol)
            await self._exchange.set_leverage(symbol, leverage)
            return {"status": "closed", "action_needed": False}
        
        # Existing position with same side - adding to position
        return {
            "status": "compatible", 
            "action_needed": False, 
            "current_position": position
        }

    @error_handler(
        context_extractor=lambda self, symbol, control_type: {"symbol": symbol, "control_type": control_type},
        log_message="Position control failed"
    )
    async def position_control(self, symbol: str, control_type: str) -> Dict[str, Any]:
        """
        Manage an existing position by cancelling pending orders and closing the position.

        After closing the position, the method resets the position mode.
        """
        await self._ensure_initialized()
        cancel_result = await self._exchange.cancel_all_orders(symbol)
        if not cancel_result.get("success"):
            raise ValidationError("Failed to cancel orders", context={"symbol": symbol, "cancel_result": cancel_result})
        close_result = await self._exchange.close_position(symbol)
        if not close_result.get("success"):
            raise ValidationError("Failed to close position", context={"symbol": symbol, "close_result": close_result})
        await self._exchange.set_position_mode()
        self.logger.info("Position control executed", extra={"symbol": symbol, "type": control_type})
        return {"success": True, "cancel_result": cancel_result, "close_result": close_result}

    @error_handler(
        context_extractor=lambda self, symbol, side, client_id, leverage, **kwargs: {
            "symbol": symbol,
            "side": side,
            "client_id": client_id,
            "leverage": leverage
        },
        log_message="Failed to place signal order"
    )
    async def place_signal(
        self,
        symbol: str,
        side: str,
        size: str,
        client_id: str,
        leverage: str,
        take_profit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place a signal trade.

        This method first checks and adjusts any existing position, then places a limit order.
        """
        await self._ensure_initialized()
        position_result = await self.handle_current_position(symbol, side, leverage)
        self._check_position_result(symbol, position_result)
        entry_price = await self._get_entry_price(symbol, side)
        order_params = {
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "size": size,
            "price": str(entry_price),
            "client_id": client_id,
            "leverage": leverage,
        }
        if take_profit:
            validated_tp = await self._validate_price(symbol, side, "take_profit", Decimal(take_profit))
            order_params["take_profit"] = str(validated_tp)
        order_result = await self._execute_order(order_params)
        
        # Calculate position size for response
        order_size = Decimal(size) * Decimal(str(entry_price))
        
        self.logger.info("Signal order placed", extra={"symbol": symbol, "side": side, "size": size})
        return {
            "success": True, 
            "order": order_result, 
            "position": position_result,
            "size": size,
            "entry_price": str(entry_price),
            "order_size": str(order_size)
        }

    @error_handler(
        context_extractor=lambda self, symbol, side, size, client_id, take_profit, leverage: {
            "symbol": symbol,
            "side": side,
            "client_id": client_id,
            "leverage": leverage,
            "take_profit": take_profit
        },
        log_message="Failed to place ladder order"
    )
    async def place_ladder(
        self,
        symbol: str,
        side: str,
        size: str,
        client_id: str,
        take_profit: str,
        leverage: str,
    ) -> Dict[str, Any]:
        """
        Place a ladder trade.

        This method cancels any existing orders, then places a ladder order with a take profit.
        """
        await self._ensure_initialized()
        position_result = await self.handle_current_position(symbol, side, leverage)
        self._check_position_result(symbol, position_result)
        await self._exchange.cancel_all_orders(symbol)
        entry_price = await self._get_entry_price(symbol, side)
        validated_tp = await self._validate_price(symbol, side, "take_profit", Decimal(take_profit))
        order_params = {
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "size": size,
            "price": str(entry_price),
            "client_id": client_id,
            "leverage": leverage,
            "take_profit": str(validated_tp),
        }
        order_result = await self._execute_order(order_params)
        
        # Calculate order size for response
        order_size = Decimal(size) * Decimal(str(entry_price))
        
        self.logger.info("Ladder order placed", extra={"symbol": symbol, "side": side, "size": size})
        return {
            "success": True, 
            "order": order_result, 
            "position": position_result,
            "size": size,
            "entry_price": str(entry_price),
            "order_size": str(order_size)
        }

    async def _calc_trade_size(self, symbol: str, risk_percentage: str, leverage: str, balance: Decimal) -> Decimal:
        """
        Calculate trade size (quantity) based on risk%, leverage, and account balance.
        
        Args:
            symbol: Trading symbol
            risk_percentage: Risk percentage as string (e.g., "1.5")
            leverage: Leverage as string (e.g., "10")
            balance: Account balance as Decimal
            
        Returns:
            Decimal representing the trade size (quantity of the asset to trade)
        
        Example:
            For BTC with risk=1%, leverage=10, balance=$10,000
            If BTC price = $50,000 and lot_size = 0.001
            Risk amount = $10,000 * 0.01 = $100
            Raw size = ($100 * 10) / ($50,000 * 1) = 0.02 BTC
            Valid size = 0.02 rounded to nearest 0.001 = 0.02 BTC
        """
        try:
            risk_pct = Decimal(risk_percentage)
            leverage_val = Decimal(leverage)
            if risk_pct <= 0 or leverage_val <= 0:
                raise ValidationError("Risk and leverage must be positive", context={"risk_percentage": risk_percentage, "leverage": leverage})
            if balance <= 0:
                raise ValidationError("Insufficient balance", context={"balance": str(balance)})
            
            # Get symbol specifications
            specs = await self._get_symbol_specs(symbol)
            lot_size = Decimal(specs["specifications"]["lot_size"])
            contract_size = Decimal(specs["specifications"]["contract_size"])
            
            # Calculate risk amount in account currency
            risk_amount = balance * (risk_pct / Decimal("100"))
            
            # Get current market price
            prices = await self._exchange.get_current_price(symbol)
            price = Decimal(prices["last_price"])
            
            # Calculate raw size
            raw_size = (risk_amount * leverage_val) / (contract_size * price)
            
            # Round to valid lot size
            valid_size = (raw_size // lot_size) * lot_size
            
            # Ensure minimum size
            if valid_size < lot_size:
                return lot_size
                
            self.logger.debug(
                "Calculated trade size", 
                extra={
                    "symbol": symbol,
                    "risk_percentage": str(risk_pct),
                    "leverage": str(leverage_val),
                    "balance": str(balance),
                    "price": str(price),
                    "raw_size": str(raw_size),
                    "valid_size": str(valid_size)
                }
            )
            
            return valid_size
        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError("Trade size calculation failed", context={"symbol": symbol, "risk%": risk_percentage, "leverage": leverage, "error": str(e)}) from e

    async def _validate_price(self, symbol: str, side: str, price_type: str, price: Decimal) -> Decimal:
        """
        Validate and normalize the provided price based on the exchange's tick size.

        This method is not decorated as it is a low-level helper.
        """
        try:
            if price <= 0:
                raise ValidationError("Price must be positive", context={"price": str(price), "symbol": symbol, "side": side})
            specs = await self._get_symbol_specs(symbol)
            tick_size = Decimal(specs["specifications"]["tick_size"])
            normalized = (price / tick_size).to_integral_value() * tick_size
            self.logger.debug("Validated price", extra={"symbol": symbol, "original": str(price), "normalized": str(normalized), "price_type": price_type})
            return normalized
        except ValidationError:
            raise
        except Exception as e:
            raise ExchangeError("Price validation failed", context={"symbol": symbol, "price": str(price), "side": side, "price_type": price_type, "error": str(e)}) from e

    async def _execute_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an order by delegating to the exchange's place_order method and monitor its status.

        This helper is not decorated as it is used internally.
        """
        try:
            result = await self._exchange.place_order(order_params)
            if result.get("order_id"):
                monitor_result = await self._monitor_order(symbol=order_params["symbol"], order_id=result["order_id"])
                result["monitor_status"] = monitor_result
            return result
        except Exception as e:
            raise ExchangeError("Order execution failed", context={"params": order_params, "error": str(e)}) from e

    async def _monitor_order(self, symbol: str, order_id: str, max_attempts: int = 9, check_interval: float = 1.0) -> Dict[str, Any]:
        """
        Monitor an order until it is filled or a timeout is reached.

        This helper is not decorated.
        """
        try:
            attempt = 0
            while attempt < max_attempts:
                await asyncio.sleep(check_interval)
                order_info = await self._exchange.get_order_status(symbol=symbol, order_id=order_id)
                if not order_info:
                    return {"status": "filled"}
                current_prices = await self._exchange.get_current_price(symbol)
                order_price = Decimal(order_info["price"])
                side = order_info["side"].lower()
                price_diff_threshold = Decimal("0.001")
                needs_update = False
                new_price = order_price
                if side == "buy":
                    if Decimal(current_prices["last_price"]) > order_price * (1 + price_diff_threshold):
                        needs_update = True
                        new_price = current_prices["bid_price"]
                else:
                    if Decimal(current_prices["last_price"]) < order_price * (1 - price_diff_threshold):
                        needs_update = True
                        new_price = current_prices["ask_price"]
                if needs_update:
                    try:
                        await self._exchange.amend_order(symbol=symbol, order_id=order_id, new_price=new_price)
                    except Exception as e:
                        self.logger.warning("Failed to amend order", extra={"symbol": symbol, "order_id": order_id, "attempt": attempt, "error": str(e)})
                attempt += 1
            return {"status": "timeout", "attempts": attempt}
        except Exception as e:
            raise ExchangeError("Order monitoring failed", context={"symbol": symbol, "order_id": order_id, "error": str(e)}) from e

    async def _record_trade(self, symbol: str, side: str, size: str, order_result: Dict[str, Any], source: Any) -> None:
        """
        Record the executed trade in the account's trade history.
        """
        try:
            await self.account.record_trade(
                symbol=symbol,
                side=side,
                size=size,
                entry_price=order_result["entry_price"],
                source=source
            )
        except Exception as e:
            self.logger.error("Failed to record trade", extra={"symbol": symbol, "side": side, "error": str(e)})

    async def _update_performance(self) -> None:
        """
        Update performance metrics based on closed trade history.

        This method fetches the current balance and closed trades (since midnight UTC)
        and delegates updating to the performance service.
        """
        try:
            balance_info = await self._exchange.get_balance()
            balance = Decimal(str(balance_info.get("balance", 0)))
            equity = Decimal(str(balance_info.get("equity", 0)))
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            now = datetime.utcnow()
            closed_trades = await self._exchange.get_position_history(today, now)
            total_trades = len(closed_trades)
            total_position_value = sum(Decimal(str(trade.get("notional_value", 0))) for trade in closed_trades)
            metrics = {
                "balance": balance,
                "equity": equity,
                "positions": total_trades,
                "position_value": total_position_value,
            }
            await self.performance_service.update_daily_performance(
                account_id=str(self.account_id),
                date=today,
                balance=balance,
                equity=equity,
                metrics=metrics
            )
            self.logger.info("Performance updated", extra={"account_id": self.account_id, "closed_trades": total_trades})
        except Exception as e:
            self.logger.error("Failed to update performance", extra={"account_id": self.account_id, "error": str(e)})
            raise ExchangeError("Performance update failed", context={"account_id": self.account_id, "error": str(e)}) from e

    def _check_position_result(self, symbol: str, result: Dict[str, Any]) -> None:
        """
        Check if the current position handling indicates that further action is needed.
        """
        if result.get("action_needed"):
            raise ExchangeError("Failed to handle position", context={"symbol": symbol, "result": result})

    async def _get_entry_price(self, symbol: str, side: str) -> Decimal:
        """
        Retrieve the current entry price based on the order side.
        
        Args:
            symbol: Trading symbol
            side: Trade side ("buy" or "sell")
            
        Returns:
            Current market price appropriate for the side (bid for buy, ask for sell)
        """
        prices = await self._exchange.get_current_price(symbol)
        return prices["bid_price"] if side.lower() == "buy" else prices["ask_price"]

    async def _get_symbol_specs(self, symbol: str) -> Dict[str, Any]:
        """
        Retrieve symbol specifications using the symbol validator.
        """
        return await symbol_validator.validate_symbol(symbol=symbol, exchange_type=self.account["exchange"])

    @classmethod
    @error_handler
    async def terminate_bot_accounts(cls, bot_id: str, reference_manager: Any, exchange_factory: Any) -> Dict[str, Any]:
        """
        Terminate all positions for bot accounts.

        Retrieves all accounts associated with the given bot and attempts to close all open positions.
        """
        try:
            valid = await reference_manager.validate_reference(
                source_type="ExchangeOperations",
                target_type="Bot",
                reference_id=bot_id,
            )
            if not valid:
                raise NotFoundError("Bot not found", context={"bot_id": bot_id})
            accounts = await reference_manager.get_references(source_type="Bot", reference_id=bot_id)
            results = []
            for account in accounts:
                try:
                    exchange = await exchange_factory.get_instance(str(account.id), reference_manager)
                    positions = await exchange.get_all_positions()
                    for position in positions:
                        await exchange.close_position(position["symbol"])
                    results.append({"account_id": str(account.id), "success": True, "closed_positions": len(positions)})
                except Exception as e:
                    results.append({"account_id": str(account.id), "success": False, "error": str(e)})
            success = any(r["success"] for r in results)
            logger.info("Terminated bot accounts", extra={"bot_id": bot_id, "account_count": len(results), "success": success})
            return {"success": success, "results": results, "terminated_accounts": len(results)}
        except Exception as e:
            raise ExchangeError("Failed to terminate bot accounts", context={"bot_id": bot_id, "error": str(e)}) from e

    @classmethod
    @error_handler
    async def cleanup(cls) -> None:
        """Clean up all operations resources."""
        try:
            await ws_manager.close_all()
            symbol_validator.invalidate_cache()
            logger.info("Cleaned up exchange operations")
        except Exception as e:
            logger.error("Cleanup failed", extra={"error": str(e)})