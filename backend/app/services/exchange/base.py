"""
Base exchange class providing core exchange functionality.

Features:
- Core exchange operations
- Resource management
- Rate limiting
- Error handling
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Protocol, TypeVar, Union
from decimal import Decimal
import asyncio
import aiohttp
from datetime import datetime, timedelta

class ExchangeCredentials:
    """Exchange API credentials with validation."""
    
    def __init__(
        self, 
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        testnet: bool = True
    ):
        self.validate_credentials(api_key, api_secret)      
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
    
    @staticmethod
    def validate_credentials(api_key: str, api_secret: str) -> None:
        if not api_key or not api_secret:
            raise ValidationError(
                "Missing required credentials",
                context={
                    "has_key": bool(api_key),
                    "has_secret": bool(api_secret)
                }
            )

class ExchangeProtocol(Protocol):
    """Core exchange functionality protocol."""
    
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]: ...
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]: ...
    async def get_position(self, symbol: str) -> Optional[Dict]: ...
    async def get_all_positions(self) -> List[Dict]: ...
    #async def get_position_history(self) -> List[Dict]: ...
    async def set_leverage(self, symbol: str, leverage: str) -> Dict: ...
    async def set_position_mode(self) -> Dict: ...
    async def close_position(self, symbol: str) -> Dict: ...

class BaseExchange(ABC):
    """
    Base exchange implementation providing core functionality.
    
    Features:
    - Core trading operations
    - Connection management
    - Rate limiting
    - Error handling
    """
    
    def __init__(self, credentials: ExchangeCredentials):
        """Initialize exchange with connection management."""
        try:
            self.credentials = credentials
            self.session: Optional[aiohttp.ClientSession] = None
            self._request_semaphore = asyncio.Semaphore(10)
            self._rate_limit = 10  # Requests per second
            self._last_request_time = 0
            self._timeout = aiohttp.ClientTimeout(total=30)
            self._logger = None
            self.exchange_type = None  # Set by subclasses

        except Exception as e:
            raise ExchangeError(
                "Failed to initialize exchange",
                context={
                    "exchange": self.__class__.__name__,
                    "error": str(e)
                }
            )

    @property
    def logger(self):
        """Lazy logger initialization."""
        if self._logger is None:
            self._logger = logger.getChild(self.__class__.__name__)
        return self._logger

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    @abstractmethod
    def _get_base_url(self) -> str:
        """Get exchange base URL."""
        raise NotImplementedError

    async def connect(self) -> None:
        """
        Establish HTTP session with error handling.
        
        Raises:
            ExchangeError: If connection fails
            RequestException: If initial test fails
        """
        try:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=self._timeout,
                    headers={"User-Agent": f"{self.__class__.__name__}-API/1.0"}
                )
                await self._test_connection()
                
                self.logger.info("Established HTTP session")

        except aiohttp.ClientError as e:
            raise RequestException(
                "Failed to establish connection",
                context={
                    "exchange": self.__class__.__name__,
                    "error": str(e)
                }
            )
        except Exception as e:
            raise ExchangeError(
                "Failed to connect",
                context={
                    "exchange": self.__class__.__name__,
                    "error": str(e)
                }
            )

    async def close(self) -> None:
        """Close exchange connection and cleanup resources."""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                
            self.logger.info("Closed exchange connection")

        except Exception as e:
            self.logger.error(
                "Error during cleanup",
                extra={"error": str(e)}
            )

    async def _test_connection(self) -> None:
        """
        Test exchange connection with ping endpoint.
        
        Raises:
            RequestException: If test fails
        """
        try:
            await self._execute_request("GET", "/api/v1/ping")
        except Exception as e:
            raise RequestException(
                "Connection test failed",
                context={"error": str(e)}
            )

    async def _handle_rate_limit(self) -> None:
        """
        Enforce rate limiting with exponential backoff.
        
        Raises:
            RateLimitError: If rate limit exceeded
        """
        now = datetime.now().timestamp()
        elapsed = now - self._last_request_time
        
        if elapsed < 1.0 / self._rate_limit:
            retry_count = 0
            while retry_count < 3:
                wait_time = (1.0 / self._rate_limit - elapsed) * (2 ** retry_count)
                await asyncio.sleep(wait_time)
                
                now = datetime.now().timestamp()
                elapsed = now - self._last_request_time
                if elapsed >= 1.0 / self._rate_limit:
                    break
                    
                retry_count += 1
            else:
                raise RateLimitError(
                    "Rate limit exceeded",
                    context={
                        "rate_limit": self._rate_limit,
                        "elapsed": elapsed
                    }
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
        Execute request with rate limiting and error handling.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Optional request body
            params: Optional query parameters
            
        Returns:
            Dict: Response data
            
        Raises:
            RequestException: If request fails
            RateLimitError: If rate limit exceeded
        """
        raise NotImplementedError

    @abstractmethod
    async def _fetch_symbol_info_from_exchange(
        self,
        symbol: str
    ) -> Dict[str, Decimal]:
        """
        Get symbol trading specifications.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with:
            - tick_size: Price increment
            - lot_size: Quantity increment
            - contract_size: Contract multiplier
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get current price for symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with:
            - last_price: Last traded price
            - bid_price: Best bid
            - ask_price: Best ask
            
        Raises:
            ExchangeError: If price retrieval fails
        """
        raise NotImplementedError

    @abstractmethod  
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]:
        """
        Get account balance information.
        
        Args:
            currency: Currency code
            
        Returns:
            Dict with:
            - balance: Available balance
            - equity: Account equity 
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Dict]:
        """
        Get position for symbol if exists.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with position details or None
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def get_all_positions(self) -> List[Dict]:
        """
        Get all open positions.
        
        Returns:
            List[Dict]: Open positions
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def get_position_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """
        Get closed position history.
        
        Args:
            start_time: Start time
            end_time: End time
            symbol: Optional symbol filter
            
        Returns:
            List[Dict]: Closed positions
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def get_order_status(
        self,
        symbol: str,
        order_id: str
    ) -> Optional[Dict]:
        """
        Get order status information.
        
        Args:
            symbol: Trading symbol
            order_id: Order to check
            
        Returns:
            Dict with order status or None
            
        Raises:
            ExchangeError: If retrieval fails
        """
        raise NotImplementedError

    @abstractmethod
    async def amend_order(
        self,
        symbol: str,
        order_id: str,
        new_price: Decimal
    ) -> Dict:
        """
        Modify existing order price.
        
        Args:
            symbol: Trading symbol
            order_id: Order to modify 
            new_price: Updated price
            
        Returns:
            Dict with amendment result
            
        Raises:
            ExchangeError: If modification fails
        """
        raise NotImplementedError

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: str) -> Dict:
        """
        Set leverage for symbol.
        
        Args:
            symbol: Trading symbol
            leverage: Leverage value
            
        Returns:
            Dict with leverage result
            
        Raises:
            ExchangeError: If setting fails
        """
        raise NotImplementedError

    @abstractmethod
    async def set_position_mode(self) -> Dict:
        """
        Set position mode.
        
        Returns:
            Dict with mode result
            
        Raises:
            ExchangeError: If setting fails
        """
        raise NotImplementedError

    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        """
        Cancel all pending orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            Dict with cancellation result
            
        Raises:
            ExchangeError: If cancellation fails
        """
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, symbol: str) -> Dict:
        """
        Close open position.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with closure result
            
        Raises:
            ExchangeError: If closure fails
        """
        raise NotImplementedError

# Move imports to end to avoid circular imports
from app.core.errors import (
    ExchangeError,
    ValidationError,
    RequestException,
    RateLimitError
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)