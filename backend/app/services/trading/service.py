"""
Trading Service

This service acts as a factory for ExchangeOperations instances and provides
a simplified interface for common trading operations.

It centralizes the creation of ExchangeOperations objects with all required
dependencies and ensures consistent parameter handling across the application.
"""

from typing import Dict, List, Optional, Any, Union
from decimal import Decimal

from app.core.errors.base import ExchangeError, ValidationError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger
from app.core.references import TradeSource, OrderType

# Import dependencies that will be injected into ExchangeOperations
from app.services.exchange.factory import exchange_factory
from app.services.exchange.operations import ExchangeOperations
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)


class TradingService:
    """
    Factory service for ExchangeOperations that provides simplified access
    to trading functionality across the application.
    """

    def __init__(self) -> None:
        """Initialize the trading service."""
        self.logger = logger
        self.logger.info("Initializing Trading Service")

    @error_handler(
        context_extractor=lambda self, account_id: {"account_id": account_id},
        log_message="Failed to get exchange operations"
    )
    async def get_operations(self, account_id: str) -> ExchangeOperations:
        """
        Get an ExchangeOperations instance for the given account with all dependencies injected.
        
        Args:
            account_id: ID of the account to create operations for
            
        Returns:
            Configured ExchangeOperations instance
            
        Raises:
            ExchangeError: If account setup fails
        """
        return ExchangeOperations(
            account_id=account_id,
            exchange_factory=exchange_factory,
            reference_manager=reference_manager,
            ws_manager=ws_manager,
            performance_service=performance_service
        )

    @error_handler(
        context_extractor=lambda self, account_id, symbol, risk_percentage, leverage: {
            "account_id": account_id,
            "symbol": symbol,
            "risk_percentage": risk_percentage, 
            "leverage": leverage
        },
        log_message="Size calculation failed"
    )
    async def calculate_trade_size(
        self,
        account_id: str,
        symbol: str,
        risk_percentage: Union[str, float],
        leverage: Union[str, int]
    ) -> str:
        """
        Calculate trade size based on risk percentage, leverage, and account balance.
        
        This centralizes the size calculation logic and shields API endpoints
        from implementation details.
        
        Args:
            account_id: ID of the account to calculate size for
            symbol: Trading symbol
            risk_percentage: Risk percentage relative to account balance
            leverage: Position leverage
            
        Returns:
            Calculated trade size as a string
            
        Raises:
            ExchangeError: If calculation fails
            ValidationError: If parameters are invalid
        """
        # Get operations instance
        ops = await self.get_operations(account_id)
        
        # Get account balance
        balance = await ops._exchange.get_balance()
        current_balance = balance["balance"]
        
        # Calculate size using the private method in operations
        size = await ops._calc_trade_size(
            symbol=symbol,
            risk_percentage=str(risk_percentage),
            leverage=str(leverage),
            balance=Decimal(str(current_balance))
        )
        
        self.logger.info(
            "Calculated trade size",
            extra={
                "account_id": account_id,
                "symbol": symbol,
                "risk_percentage": risk_percentage,
                "leverage": leverage,
                "size": str(size)
            }
        )
        
        return str(size)

    @error_handler(
        context_extractor=lambda self, account_id, **kwargs: {"account_id": account_id, "params": kwargs},
        log_message="Trade execution failed"
    )
    async def execute_trade(
        self,
        account_id: str,
        symbol: str,
        side: str,
        order_type: OrderType,
        risk_percentage: Union[str, float],
        leverage: Union[str, int],
        take_profit: Optional[Union[str, float]] = None,
        stop_loss: Optional[Union[str, float]] = None,
        source: TradeSource = TradeSource.TRADING_PANEL,
        pre_calculated_size: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a trade for the given account using exchange operations.
        
        Args:
            account_id: ID of the account to trade with
            symbol: Trading symbol
            side: Trade side (buy/sell)
            order_type: Order type
            risk_percentage: Risk percentage
            leverage: Position leverage
            take_profit: Optional take profit price
            stop_loss: Optional stop loss price
            source: Trade source identifier
            pre_calculated_size: Optional pre-calculated size to use instead of calculating from risk
            
        Returns:
            Dict with trade execution result including:
            - size: Trade size (quantity of asset)
            - entry_price: Price at which trade was executed
            - order_size: Order size in USD value (size * entry_price)
            - order_id: Exchange order ID
            - success flag and additional details
            
        Raises:
            ExchangeError: If trade execution fails
            ValidationError: If parameters are invalid
        """
        # Convert parameters to strings for consistency
        risk_percentage_str = str(risk_percentage)
        leverage_str = str(leverage)
        take_profit_val = str(take_profit) if take_profit is not None else None
        stop_loss_val = str(stop_loss) if stop_loss is not None else None
        
        # Get operations instance
        ops = await self.get_operations(account_id)
        
        # Use pre-calculated size or calculate it now
        size = pre_calculated_size
        if size is None:
            size = await self.calculate_trade_size(
                account_id=account_id,
                symbol=symbol,
                risk_percentage=risk_percentage,
                leverage=leverage
            )
        
        # Execute trade
        result = await ops.execute_trade(
            symbol=symbol,
            side=side,
            order_type=order_type,
            risk_percentage=risk_percentage_str,
            leverage=leverage_str,
            take_profit=take_profit_val,
            stop_loss=stop_loss_val,
            source=source,
            size=size  # Pass the calculated size
        )
        
        self.logger.info(
            "Trade executed",
            extra={
                "account_id": account_id,
                "symbol": symbol,
                "side": side,
                "order_id": result.get("order_id"),
                "size": result.get("size"),
                "order_size": result.get("order_size")
            }
        )
        
        return result

    @error_handler(
        context_extractor=lambda self, account_id, symbol, order_type=None, manual_price=None: {
            "account_id": account_id,
            "symbol": symbol
        },
        log_message="Position close failed"
    )
    async def close_position(
        self,
        account_id: str,
        symbol: str,
        order_type: Optional[OrderType] = None,
        manual_price: Optional[Union[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Close a position for the given account and symbol.
        
        Args:
            account_id: ID of the account
            symbol: Symbol of the position to close
            order_type: Optional order type for closing
            manual_price: Optional manual exit price
            
        Returns:
            Dict with close result
            
        Raises:
            ExchangeError: If closing fails
        """
        # Get operations instance
        ops = await self.get_operations(account_id)
        
        # Close the position
        result = await ops.position_control(
            symbol=symbol,
            control_type="close"
        )
        
        # Get exit price if not provided
        if not manual_price:
            prices = await ops._exchange.get_current_price(symbol)
            exit_price = prices.get("last_price")
        else:
            exit_price = manual_price
        
        # Add exit price to result
        result["exit_price"] = exit_price
        
        self.logger.info(
            "Position closed",
            extra={
                "account_id": account_id,
                "symbol": symbol,
                "exit_price": exit_price
            }
        )
        
        return result

    @error_handler(
        context_extractor=lambda self, account_id: {"account_id": account_id},
        log_message="Account termination failed"
    )
    async def terminate_account(
        self,
        account_id: str
    ) -> Dict[str, Any]:
        """
        Terminate all positions for an account.
        
        Args:
            account_id: ID of the account to terminate positions for
            
        Returns:
            Dict with termination results
            
        Raises:
            ExchangeError: If termination fails
        """
        # Get operations instance
        ops = await self.get_operations(account_id)
        
        # Get account to get exchange instance
        account = await reference_manager.get_reference(account_id)
        if not account:
            raise ValidationError("Account not found", context={"account_id": account_id})
        
        # Get exchange instance
        exchange = await exchange_factory.get_instance(account_id, reference_manager)
        
        # Get all positions
        positions = await exchange.get_all_positions()
        
        # Terminate each position
        results = []
        for position in positions:
            symbol = position.get("symbol")
            try:
                # Close the position
                result = await ops.position_control(
                    symbol=symbol,
                    control_type="close"
                )
                
                # Get market price
                prices = await ops._exchange.get_current_price(symbol)
                
                results.append({
                    "symbol": symbol,
                    "success": True,
                    "exit_price": prices.get("last_price"),
                    "trading_fees": position.get("trading_fees", 0),
                    "funding_fees": position.get("funding_fees", 0)
                })
            except Exception as e:
                self.logger.error(
                    f"Failed to terminate position for {symbol}",
                    extra={"account_id": account_id, "symbol": symbol, "error": str(e)}
                )
                results.append({
                    "symbol": symbol,
                    "success": False,
                    "error": str(e)
                })
        
        self.logger.info(
            "Account positions terminated",
            extra={
                "account_id": account_id,
                "positions_count": len(positions),
                "success_count": sum(1 for r in results if r.get("success", False))
            }
        )
        
        return {
            "success": len(results) > 0,
            "positions": results,
            "positions_terminated": sum(1 for r in results if r.get("success", False))
        }

    @error_handler(
        context_extractor=lambda self, bot_id, signal_data, context=None: {
            "bot_id": bot_id,
            "symbol": signal_data.get("symbol"),
            "side": signal_data.get("side")
        },
        log_message="Signal processing failed"
    )
    async def process_signal(
        self,
        bot_id: str,
        signal_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process a trading signal for a bot.
        
        This method determines whether to use place_signal or place_ladder
        based on the signal_type, and calculates size from risk_percentage.
        Works with both automated (webhook) and manual trading bots.
        
        Args:
            bot_id: ID of the bot
            signal_data: Signal data including:
                - symbol: Trading symbol
                - side: Position side (LONG/SHORT)
                - signal_type: SignalOrderType (LONG_SIGNAL, SHORT_SIGNAL, LONG_LADDER, SHORT_LADDER)
                - risk_percentage: Risk percentage relative to balance
                - leverage: Position leverage
                - take_profit: Optional take profit level
            context: Optional context for logging
                
        Returns:
            Dict with processing results
            
        Raises:
            ExchangeError: If signal processing fails
        """
        # Get bot and check its type
        bot = await reference_manager.get_reference(bot_id)
        if not bot:
            raise ValidationError("Bot not found", context={"bot_id": bot_id})
                
        accounts = bot.get("connected_accounts", [])
        if not accounts:
            return {
                "success": True,
                "message": "No accounts connected to bot",
                "accounts_processed": 0,
                "success_count": 0,
                "error_count": 0,
                "total_signals": 0,
                "successful_signals": 0,
                "failed_signals": 0,
                "results": []
            }
                
        # Extract signal parameters
        symbol = signal_data.get("symbol")
        side = signal_data.get("side")
        signal_type = signal_data.get("signal_type")
        risk_percentage = signal_data.get("risk_percentage")
        leverage = signal_data.get("leverage")
        take_profit = signal_data.get("take_profit")
        
        # Determine the signal source based on bot type
        bot_type = bot.get("bot_type", "automated")  # Default to automated if not specified
        source = TradeSource.BOT if bot_type == "automated" else TradeSource.TRADING_PANEL
        
        # Override with explicit source if provided
        if signal_data.get("source"):
            source = signal_data.get("source")
        
        # Determine if this is a ladder signal
        is_ladder = signal_type in [
            SignalOrderType.LONG_LADDER,
            SignalOrderType.SHORT_LADDER
        ]
        
        # Calculate size if not provided (centralized calculation)
        size = signal_data.get("size")
        if size is None and risk_percentage is not None and leverage is not None:
            # Use the first account for sample size calculation
            try:
                size = await self.calculate_trade_size(
                    account_id=accounts[0],
                    symbol=symbol,
                    risk_percentage=risk_percentage,
                    leverage=leverage
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to pre-calculate size: {str(e)}",
                    extra={"bot_id": bot_id, "symbol": symbol}
                )
                # Size will be calculated individually for each account
        
        # Process each account
        results = []
        success_count = 0
        error_count = 0
        
        for account_id in accounts:
            try:
                # Get exchange operations instance
                ops = await self.get_operations(account_id)
                
                # Choose between place_signal and place_ladder based on signal_type
                if is_ladder:
                    # Execute ladder order
                    trade_result = await ops.place_ladder(
                        symbol=symbol,
                        side=side,
                        size=size,
                        client_id=f"{bot_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                        take_profit=take_profit,
                        leverage=leverage
                    )
                else:
                    # Execute regular signal order
                    trade_result = await ops.place_signal(
                        symbol=symbol,
                        side=side,
                        size=size,
                        client_id=f"{bot_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                        leverage=leverage,
                        take_profit=take_profit
                    )
                
                results.append({
                    "account_id": account_id,
                    "success": True,
                    "details": trade_result
                })
                success_count += 1
                
            except Exception as e:
                self.logger.error(
                    f"Failed to process signal for account {account_id}",
                    extra={"bot_id": bot_id, "account_id": account_id, "error": str(e)}
                )
                
                results.append({
                    "account_id": account_id,
                    "success": False,
                    "error": str(e)
                })
                error_count += 1
        
        # Return comprehensive results
        return {
            "success": error_count == 0,
            "accounts_processed": len(accounts),
            "success_count": success_count,
            "error_count": error_count,
            "total_signals": 1,
            "successful_signals": 1 if success_count > 0 else 0,
            "failed_signals": 1 if success_count == 0 else 0,
            "results": results
        }


# Global instance for use throughout the application
trading_service = TradingService()