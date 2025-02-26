"""
Trading Service

This service coordinates high-level trading operations by integrating with the exchange service layer.
It provides methods to execute trades and manage orders and positions.
"""

from app.services.exchange.operations import ExchangeOperations
from app.core.logging.logger import get_logger
from app.core.errors.base import ExchangeError, ValidationError
from app.services.reference.manager import reference_manager

logger = get_logger(__name__)

class TradingService:
    def __init__(self) -> None:
        self.logger = logger
        self.logger.info("Initializing Trading Service")

    async def execute_trade(self, account_id: str, trade_params: dict) -> dict:
        """
        Execute a trade for the given account using exchange operations.

        Args:
            account_id: The identifier of the account to trade with.
            trade_params: A dictionary of parameters required for executing the trade.
                          (This may include keys such as symbol, side, order_type, risk_percentage, leverage, etc.)

        Returns:
            A dictionary with the result of the trade execution.

        Raises:
            ExchangeError: If the trade execution fails.
        """
        try:
            # Create an instance of ExchangeOperations for this account.
            # (Note: If your implementation of ExchangeOperations requires additional dependencies
            # such as a WebSocket manager or performance service, pass them here.)
            exchange_ops = ExchangeOperations(account_id, reference_manager)
            result = await exchange_ops.execute_trade(**trade_params)
            self.logger.info("Trade executed", extra={"account_id": account_id, "trade_result": result})
            return result
        except Exception as e:
            self.logger.error("Failed to execute trade", extra={"account_id": account_id, "error": str(e)})
            raise ExchangeError("Trade execution failed", context={"account_id": account_id, "error": str(e)}) from e

# Global instance of the trading service
trading_service = TradingService()
