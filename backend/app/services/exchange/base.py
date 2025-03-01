"""
Base exchange class providing core exchange functionality.

Features:
- Core exchange operations
- Resource management
- Rate limiting
- Error handling via a global decorator
"""

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Protocol

import aiohttp
from pydantic import BaseModel, Field

from app.core.errors.decorators import error_handler
from app.core.errors.base import RateLimitError


class ExchangeCredentials(BaseModel):
    """
    Exchange API credentials.
    """
    api_key: str = Field(..., min_length=1, description="API key")
    api_secret: str = Field(..., min_length=1, description="API secret")
    passphrase: Optional[str] = Field(None, description="Optional passphrase")
    testnet: bool = Field(True, description="Flag to indicate testnet usage")


class ExchangeProtocol(Protocol):
    """Core exchange functionality protocol."""
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]: ...
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]: ...
    async def get_position(self, symbol: str) -> Optional[Dict]: ...
    async def get_all_positions(self) -> List[Dict]: ...
    async def set_leverage(self, symbol: str, leverage: str) -> Dict: ...
    async def set_position_mode(self) -> Dict: ...
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict: ...
    async def close_position(self, symbol: str) -> Dict: ...


class BaseExchange(ABC):
    """
    Base exchange implementation providing core functionality.

    Features:
    - Core trading operations
    - Connection management
    - Rate limiting
    - Error handling via decorators
    """

    def __init__(self, credentials: ExchangeCredentials) -> None:
        self.credentials = credentials
        self.session: Optional[aiohttp.ClientSession] = None
        self._rate_limit = 10  # Requests per second
        self._last_request_time: float = 0.0
        self._timeout = aiohttp.ClientTimeout(total=30)
        self._logger = None
        self.exchange_type = None  # Should be set by subclasses

    @property
    def logger(self):
        """Lazy logger initialization."""
        if self._logger is None:
            from app.core.logging.logger import get_logger
            self._logger = get_logger(self.__class__.__name__)
        return self._logger

    async def __aenter__(self) -> "BaseExchange":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    @abstractmethod
    def _get_base_url(self) -> str:
        """Get exchange base URL."""
        ...

    @error_handler(
        context_extractor=lambda self: {"exchange": self.__class__.__name__},
        log_message="Failed to connect"
    )
    async def connect(self) -> None:
        """
        Establish HTTP session.
        """
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": f"{self.__class__.__name__}-API/1.0"}
            )
            await self._test_connection()
            self.logger.info("Established HTTP session")

    @error_handler(
        context_extractor=lambda self: {"exchange": self.__class__.__name__},
        log_message="Failed to close connection"
    )
    async def close(self) -> None:
        """
        Close exchange connection and clean up resources.
        """
        if self.session and not self.session.closed:
            await self.session.close()
        self.logger.info("Closed exchange connection")

    @error_handler(
        context_extractor=lambda self: {"endpoint": "/api/v1/ping"},
        log_message="Connection test failed"
    )
    async def _test_connection(self) -> None:
        """
        Test the exchange connection using the ping endpoint.
        """
        await self._execute_request("GET", "/api/v1/ping")

    @error_handler(
        context_extractor=lambda self: {"rate_limit": self._rate_limit},
        log_message="Rate limiting failed"
    )
    async def _handle_rate_limit(self) -> None:
        """
        Enforce rate limiting with exponential backoff.

        Raises:
            RateLimitError: If rate limit is exceeded after retries.
        """
        min_interval = 1.0 / self._rate_limit
        now = datetime.now().timestamp()
        elapsed = now - self._last_request_time

        if elapsed < min_interval:
            for retry in range(3):
                wait_time = (min_interval - elapsed) * (2 ** retry)
                await asyncio.sleep(wait_time)
                now = datetime.now().timestamp()
                elapsed = now - self._last_request_time
                if elapsed >= min_interval:
                    break
            else:
                raise RateLimitError(
                    "Rate limit exceeded",
                    context={"rate_limit": self._rate_limit, "elapsed": elapsed}
                )
        self._last_request_time = now

    @abstractmethod
    async def _execute_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict:
        """
        Execute a request with rate limiting and error handling.

        Args:
            method: HTTP method.
            endpoint: API endpoint.
            data: Optional request body.
            params: Optional query parameters.

        Returns:
            Response data as a dictionary.
        """
        ...

    @abstractmethod
    async def _fetch_symbol_info_from_exchange(self, symbol: str) -> Dict[str, Decimal]:
        """
        Retrieve symbol trading specifications.

        Args:
            symbol: Trading symbol.

        Returns:
            A dictionary with keys:
              - tick_size: Price increment.
              - lot_size: Quantity increment.
              - contract_size: Contract multiplier.
        """
        ...

    @abstractmethod
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get the current price for a symbol.

        Args:
            symbol: Trading symbol.

        Returns:
            A dictionary with keys:
              - last_price: Last traded price.
              - bid_price: Best bid.
              - ask_price: Best ask.
        """
        ...

    @abstractmethod
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]:
        """
        Get account balance information.

        Args:
            currency: Currency code.

        Returns:
            A dictionary with keys:
              - balance: Available balance.
              - equity: Account equity.
        """
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Dict]:
        """
        Get the position for a symbol if it exists.

        Args:
            symbol: Trading symbol.

        Returns:
            A dictionary with position details or None.
        """
        ...

    @abstractmethod
    async def get_all_positions(self) -> List[Dict]:
        """
        Retrieve all open positions.

        Returns:
            A list of dictionaries representing open positions.
        """
        ...

    @abstractmethod
    async def get_position_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """
        Retrieve closed position history.

        Args:
            start_time: Start time.
            end_time: End time.
            symbol: Optional symbol filter.

        Returns:
            A list of dictionaries representing closed positions.
        """
        ...

    @abstractmethod
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """
        Get the status of an order.

        Args:
            symbol: Trading symbol.
            order_id: Identifier of the order.

        Returns:
            A dictionary with the order status or None.
        """
        ...

    @abstractmethod
    async def amend_order(self, symbol: str, order_id: str, new_price: Decimal) -> Dict:
        """
        Amend an existing order's price.

        Args:
            symbol: Trading symbol.
            order_id: Identifier of the order.
            new_price: Updated price.

        Returns:
            A dictionary with the amendment result.
        """
        ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: str) -> Dict:
        """
        Set leverage for a symbol.

        Args:
            symbol: Trading symbol.
            leverage: Leverage value.

        Returns:
            A dictionary with the leverage setting result.
        """
        ...

    @abstractmethod
    async def set_position_mode(self) -> Dict:
        """
        Set the position mode.

        Returns:
            A dictionary with the position mode setting result.
        """
        ...

    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        """
        Cancel all pending orders.

        Args:
            symbol: Optional symbol filter.

        Returns:
            A dictionary with the cancellation result.
        """
        ...

    @abstractmethod
    async def close_position(self, symbol: str) -> Dict:
        """
        Close an open position.

        Args:
            symbol: Trading symbol.

        Returns:
            A dictionary with the closure result.
        """
        ...
