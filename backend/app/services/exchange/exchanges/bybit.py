"""
This module provides the BybitExchange class, a concrete subclass of BaseExchange for integrating with the Bybit API.

BybitExchange:
- Handles public and private endpoints according to test/mainnet settings
- Authenticates requests with HMAC-SHA256 signatures
- Implements methods for market data, account data, positions, and orders
- Uses `_handle_error` for consistent exception handling and logging
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
import hmac
import hashlib
import json
import asyncio
from datetime import datetime

class BybitExchange(BaseExchange):
    """
    Bybit-specific exchange client implementing BaseExchange.

    Behavior:
    - Sets exchange_type to BYBIT
    - Uses Bybit's V5 API endpoints 
    - Signs requests using timestamp, api_key, and secret
    - Implements required methods for market data, positions, and orders

    Attributes:
        exchange_type (ExchangeType): Set to ExchangeType.BYBIT
        _rate_limit (int): Adjusted rate limit for requests
    """

    def __init__(self, credentials: ExchangeCredentials):
        """Initialize BybitExchange."""
        try:
            super().__init__(credentials)
            self.exchange_type = ExchangeType.BYBIT
            self._rate_limit = 20
            self.logger = logger.getChild(f"bybit_exchange")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"credentials": credentials.__dict__},
                log_message="Failed to initialize Bybit exchange"
            )
            raise

    def _get_base_url(self) -> str:
        """Get the base URL for Bybit API according to testnet setting."""
        return "https://api-testnet.bybit.com" if self.credentials.testnet else "https://api.bybit.com"

    async def _sign_request(self, timestamp: str, method: str, endpoint: str, data: Dict = {}) -> Dict[str, str]:
        """
        Generate Bybit-specific request signature with error handling.

        Args:
            timestamp: Current timestamp in milliseconds
            method: HTTP method (GET/POST)
            endpoint: API endpoint path
            data: Request body (optional)

        Returns:
            Dict with authorization headers

        Raises:
            ExchangeError: If signing fails
        """
        try:
            param_str = timestamp + self.credentials.api_key + "5000" + json.dumps(data)
            
            signature = hmac.new(
                self.credentials.api_secret.encode('utf-8'),
                param_str.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            return {
                'X-BAPI-API-KEY': self.credentials.api_key,
                'X-BAPI-SIGN': signature,
                'X-BAPI-SIGN-TYPE': '2',
                'X-BAPI-TIMESTAMP': timestamp,
                'X-BAPI-RECV-WINDOW': '5000',
                'Content-Type': 'application/json'
            }
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "exchange": self.exchange_type,
                    "endpoint": endpoint,
                    "method": method
                },
                log_message="Failed to sign request"
            )
            raise ExchangeError(
                "Failed to sign request",
                context={
                    "exchange": self.exchange_type,
                    "endpoint": endpoint,
                    "error": str(e)
                }
            )

    async def _execute_request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        params: Dict = None
    ) -> Dict:
        """
        Execute an HTTP request to Bybit's API with error handling.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Optional request body
            params: Optional query parameters

        Returns:
            Dict: Response data

        Raises:
            RequestException: For HTTP or rate limit errors
            ExchangeError: For exchange-specific errors
        """
        if self.session is None:
            await self.connect()

        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(datetime.utcnow().timestamp() * 1000))
        headers = await self._sign_request(timestamp, method, endpoint, data or {})

        try:
            await self._handle_rate_limit()
            
            async with self._request_semaphore:
                async with self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data if method == "POST" else None,
                    params=params if method == "GET" else None
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result.get("retCode") != 0:
                        await handle_api_error(
                            error=RequestException(
                                result.get("retMsg", "Unknown error"),
                                context={
                                    "response": result,
                                    "endpoint": endpoint,
                                    "exchange": self.exchange_type
                                }
                            ),
                            context={
                                "response": result,
                                "endpoint": endpoint
                            },
                            log_message=f"API request failed: {endpoint}"
                        )
                        raise RequestException(
                            result.get("retMsg", "Unknown error"),
                            context={
                                "response": result,
                                "endpoint": endpoint,
                                "exchange": self.exchange_type
                            }
                        )
                    
                    return result.get("result", {})

        except RateLimitError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "method": method,
                    "endpoint": endpoint,
                    "data": data,
                    "params": params,
                    "exchange": self.exchange_type
                },
                log_message="Request execution failed"
            )
            raise ExchangeError(
                "Request failed",
                context={
                    "method": method,
                    "endpoint": endpoint,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def _fetch_symbol_info_from_exchange(self, symbol: str) -> Dict[str, Decimal]:
        """
        Fetch symbol specifications from Bybit with error handling.

        Args:
            symbol: The trading symbol

        Returns:
            Dict with tick_size, lot_size, contract_size

        Raises:
            ExchangeError: If specification fetch fails
            ValidationError: If returned data is invalid
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/market/instruments-info",
                params={
                    "symbol": symbol,
                    "category": "linear"
                }
            )
            
            if not response.get("list"):
                raise ExchangeError(
                    "Symbol not found",
                    context={
                        "symbol": symbol,
                        "exchange": "bybit"
                    }
                )
                
            instrument = response["list"][0]
            
            try:
                tick_size = Decimal(instrument["priceFilter"]["tickSize"])
                lot_size = Decimal(instrument["lotSizeFilter"]["qtyStep"])
                contract_size = Decimal("1")  # Linear perpetuals use 1 as contract size
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "symbol": symbol,
                        "instrument_data": instrument
                    },
                    log_message="Invalid symbol specifications"
                )
                raise ValidationError(
                    "Invalid symbol specifications",
                    context={
                        "symbol": symbol,
                        "instrument_data": instrument,
                        "error": str(e)
                    }
                )

            return {
                "tick_size": tick_size,
                "lot_size": lot_size,
                "contract_size": contract_size
            }

        except (ExchangeError, ValidationError):
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type
                },
                log_message="Failed to fetch symbol information"
            )
            raise ExchangeError(
                "Failed to fetch symbol information",
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get current price data for a symbol with error handling.

        Args:
            symbol: The trading symbol

        Returns:
            Dict with last_price, bid_price, ask_price

        Raises:
            ExchangeError: If price data fetch fails
            ValidationError: If price data is invalid
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/market/tickers",
                params={
                    "symbol": symbol,
                    "category": "linear"
                }
            )
            
            if not response.get("list"):
                raise ExchangeError(
                    "No price data available",
                    context={
                        "symbol": symbol,
                        "exchange": "bybit"
                    }
                )
                
            data = response["list"][0]
            
            try:
                return {
                    "last_price": Decimal(data["lastPrice"]),
                    "bid_price": Decimal(data["bid1Price"]),
                    "ask_price": Decimal(data["ask1Price"])
                }
            except (KeyError, DecimalException) as e:
                await handle_api_error(
                    error=e,
                    context={
                        "symbol": symbol,
                        "price_data": data
                    },
                    log_message="Invalid price data format"
                )
                raise ValidationError(
                    "Invalid price data",
                    context={
                        "symbol": symbol,
                        "price_data": data,
                        "error": str(e)
                    }
                )

        except (ExchangeError, ValidationError):
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type
                },
                log_message="Failed to get current price"
            )
            raise ExchangeError(
                "Failed to get current price",
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]:
        """
        Get account balance with error handling.

        Args:
            currency: The currency to get balance for (default: USDT)

        Returns:
            Dict with balance, equity, account_mode

        Raises:
            ExchangeError: If balance fetch fails
            ValidationError: If balance data is invalid
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/account/wallet-balance",
                params={"coin": currency}
            )
            
            if not response.get("list"):
                raise ExchangeError(
                    "No balance data available",
                    context={
                        "currency": currency,
                        "exchange": self.exchange_type
                    }
                )
            
            data = response["list"][0]
            
            try:
                return {
                    "balance": Decimal(data["totalAvailableBalance"]),
                    "equity": Decimal(data["totalEquity"])
                }
            except (KeyError, DecimalException) as e:
                await handle_api_error(
                    error=e,
                    context={
                        "currency": currency,
                        "balance_data": data
                    },
                    log_message="Invalid balance data format"
                )
                raise ValidationError(
                    "Invalid balance data",
                    context={
                        "currency": currency,
                        "balance_data": data,
                        "error": str(e)
                    }
                )

        except (ExchangeError, ValidationError):
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "currency": currency,
                    "exchange": self.exchange_type
                },
                log_message="Failed to get balance"
            )
            raise ExchangeError(
                "Failed to get balance",
                context={
                    "currency": currency,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def get_all_positions(self) -> List[Dict]:
        """
        Get all open positions with error handling.

        Returns:
            List[Dict]: List of position data

        Raises:
            ExchangeError: If position fetch fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/position/list",
                params={
                    "category": "linear",
                    "settleCoin": "USDT"
                }
            )
            
            positions = response.get("list", [])
            return [pos for pos in positions if Decimal(pos.get("size", "0")) != 0]
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"exchange": self.exchange_type},
                log_message="Failed to get positions"
            )
            raise ExchangeError(
                "Failed to get positions",
                context={
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def get_position(self, symbol: str) -> Optional[Dict]:
        """
        Get position for a symbol with error handling.

        Args:
            symbol: The trading symbol

        Returns:
            Optional[Dict]: Position data if exists

        Raises:
            ExchangeError: If position fetch fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/position/list",
                params={
                    "category": "linear",
                    "symbol": symbol
                }
            )
            
            positions = response.get("list", [])
            return positions[0] if positions else None

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type
                },
                log_message="Failed to get position"
            )
            raise ExchangeError(
                "Failed to get position",
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def get_position_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """Get closed position history."""
        try:
            params = {
                "category": "linear",
                "startTime": int(start_time.timestamp() * 1000),
                "endTime": int(end_time.timestamp() * 1000)
            }
            if symbol:
                params["symbol"] = symbol

            response = await self._execute_request(
                method="GET",
                endpoint="/v5/position/closed-pnl",
                params=params
            )

            positions = []
            for pos in response.get("list", []):
                try:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": pos["side"].lower(),
                        "entry_price": Decimal(pos["avgEntryPrice"]),
                        "exit_price": Decimal(pos["avgExitPrice"]),
                        "size": Decimal(pos["size"]),
                        "raw_pnl": Decimal(pos["closedPnl"]),## need to have a look
                        "trading_fee": "",## need to have a look
                        "funding_fee": "",## need to have a look
                        "net_pnl": Decimal(pos["closedPnl"]),## need to have a look
                        "pnl_ratio": (Decimal(pos["closedPnl"]) / (Decimal(pos["size"]) * Decimal(pos["avgEntryPrice"]))) * Decimal("100"),
                        "opened_at": datetime.fromtimestamp(int(pos["createdTime"]) / 1000),
                        "closed_at": datetime.fromtimestamp(int(pos["updatedTime"]) / 1000)
                    })
                except (KeyError, DecimalException) as e:
                    self.logger.warning(
                        f"Failed to process position: {pos}",
                        extra={"error": str(e)}
                    )
                    continue

            return positions

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "date_range": f"{start_time} to {end_time}",
                    "exchange": self.exchange_type
                },
                log_message="Failed to get position history"
            )
            raise ExchangeError(
                "Failed to get position history",
                context={
                    "symbol": symbol,
                    "date_range": f"{start_time} to {end_time}",
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )
    
    async def _get_position_side(self, position: Dict) -> str:
        """
        Determine position side from position data with error handling.

        Args:
            position: Position data from Bybit

        Returns:
            str: "buy" for long positions, "sell" for shorts

        Raises:
            ValidationError: If position data is invalid
        """
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            if "side" not in position:
                raise ValidationError(
                    "Missing side in position data",
                    context={"position": position}
                )
            return "buy" if position["side"] == "Buy" else "sell"
        except ValidationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "position": position,
                    "exchange": self.exchange_type
                },
                log_message="Failed to get position side"
            )
            raise ExchangeError(
                "Failed to get position side",
                context={
                    "position": position,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def _is_position_empty(self, position: Dict) -> bool:
        """
        Check if position has zero size.

        Args:
            position: Position data from Bybit

        Returns:
            bool: True if position size is 0, False otherwise

        Raises:
            ValidationError: If position data format is invalid
            ExchangeError: If size check fails
        """
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            return Decimal(position.get("size", "0")) == Decimal("0")
        except DecimalException as e:
            raise ValidationError(
                "Invalid position size value",
                context={
                    "position": position,
                    "error": str(e)
                }
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "position": position,
                    "exchange": self.exchange_type
                },
                log_message="Failed to check position status"
            )
            raise ExchangeError(
                "Failed to check position status",
                context={
                    "position": position,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def _get_pending_orders(self, symbol: str) -> List[Dict]:
        """
        Get pending regular orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List[Dict]: List of pending orders

        Raises:
            ExchangeError: If fetching orders fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/order/realtime",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "limit": 50
                }
            )
            return response.get("list", [])
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": "bybit",
                    "error": str(e)
                },
                log_message="Failed to get pending orders"
            )
            raise ExchangeError(
                "Failed to get pending orders",
                context={
                    "symbol": symbol,
                    "exchange": "bybit",
                    "error": str(e)
                }
            )

    async def _get_pending_algo_orders(self, symbol: str) -> List[Dict]:
        """
        Get pending algorithmic and conditional orders.

        Args:
            symbol: Trading symbol

        Returns:
            List[Dict]: List of pending algo orders

        Raises:
            ExchangeError: If fetching algo orders fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/order/realtime",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "orderFilter": "StopOrder",
                    "limit": 50
                }
            )
            return response.get("list", [])
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": "bybit",
                    "error": str(e)
                },
                log_message="Failed to get pending algo orders"
            )
            raise ExchangeError(
                "Failed to get pending algo orders",
                context={
                    "symbol": symbol,
                    "exchange": "bybit", 
                    "error": str(e)
                }
            )

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """
        Get order status details with error handling.

        Args:
            symbol: Trading symbol
            order_id: Order ID to check

        Returns:
            Optional[Dict]: Order details if found

        Raises:
            ExchangeError: If order status fetch fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/v5/order/realtime",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "orderId": order_id
                }
            )
            orders = response.get("list", [])
            return next((order for order in orders if order["orderId"] == order_id), None)

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "order_id": order_id,
                    "exchange": self.exchange_type
                },
                log_message="Failed to get order status"
            )
            raise ExchangeError(
                "Failed to get order status",
                context={
                    "symbol": symbol,
                    "order_id": order_id,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def amend_order(self, symbol: str, order_id: str, new_price: Decimal) -> Dict:
        """
        Amend order price with error handling.

        Args:
            symbol: Trading symbol
            order_id: Order to amend
            new_price: New price for the order

        Returns:
            Dict: Amendment result

        Raises:
            OrderException: If amendment fails
            ValidationError: If price is invalid
        """
        try:
            if new_price <= 0:
                raise ValidationError(
                    "Price must be positive",
                    context={
                        "price": str(new_price),
                        "symbol": symbol
                    }
                )

            # Validate price against symbol specifications
            validated_price = await self.validate_price(
                symbol=symbol,
                side="buy",  # Side doesn"t matter for validation
                price_type="limit",
                price=new_price
            )

            order_params = {
                "category": "linear",
                "symbol": symbol,
                "orderId": order_id,
                "price": str(validated_price)
            }

            return await self._execute_request(
                method="POST",
                endpoint="/v5/order/amend",
                data=order_params
            )

        except ValidationError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "order_id": order_id,
                    "new_price": str(new_price),
                    "exchange": self.exchange_type
                },
                log_message="Failed to amend order"
            )
            raise ExchangeError(
                "Failed to amend order",
                context={
                    "symbol": symbol,
                    "order_id": order_id,
                    "new_price": str(new_price),
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def set_leverage(self, symbol: str, leverage: str) -> Dict:
        """
        Set leverage for symbol with error handling.

        Args:
            symbol: Trading symbol
            leverage: Desired leverage value

        Returns:
            Dict: Leverage setting result

        Raises:
            ExchangeError: If setting leverage fails
            ValidationError: If leverage is invalid
        """
        try:
            leverage_val = int(leverage)
            if leverage_val <= 0 or leverage_val > 100:
                raise ValidationError(
                    "Leverage must be between 1 and 100",
                    context={
                        "leverage": leverage,
                        "symbol": symbol
                    }
                )

            return await self._execute_request(
                method="POST",
                endpoint="/v5/position/set-leverage",
                data={
                    "symbol": symbol,
                    "buyLeverage": leverage,
                    "sellLeverage": leverage,
                    "category": "linear"
                }
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "leverage": leverage,
                    "exchange": self.exchange_type
                },
                log_message="Failed to set leverage"
            )
            raise ExchangeError(
                "Failed to set leverage",
                context={
                    "symbol": symbol,
                    "leverage": leverage,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def set_position_mode(self) -> Dict:
        """
        Set position mode with error handling.

        Returns:
            Dict: Position mode setting result

        Raises:
            ExchangeError: If setting position mode fails
        """
        try:
            return await self._execute_request(
                method="POST",
                endpoint="/v5/position/switch-mode",
                data={
                    "category": "linear",
                    "mode": 0  # Merged Single Position Mode
                }
            )
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"exchange": self.exchange_type},
                log_message="Failed to set position mode"
            )
            raise ExchangeError(
                "Failed to set position mode",
                context={
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        """
        Cancel all orders with error handling.

        Args:
            symbol: Optional symbol to limit cancellation

        Returns:
            Dict: Cancellation results

        Raises:
            ExchangeError: If cancellation fails
        """
        try:
            data = {
                "category": "linear",
                "settleCoin": "USDT"
            }
            if symbol:
                data["symbol"] = symbol

            response = await self._execute_request(
                method="POST",
                endpoint="/v5/order/cancel-all",
                data=data
            )
                
            return {
                "success": True,
                "result": response
            }
                
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type
                },
                log_message="Failed to cancel orders"
            )
            raise ExchangeError(
                "Failed to cancel orders",
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def close_position(self, symbol: str) -> Dict:
        """
        Close position with error handling.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Position closure results

        Raises:
            ExchangeError: If position closure fails
        """
        try:
            position = await self.get_position(symbol)
            if not position:
                return {
                    "success": True,
                    "result": "No position to close"
                }

            side = "Sell" if position["side"] == "Buy" else "Buy"
            
            response = await self._execute_request(
                method="POST",
                endpoint="/v5/order/create",
                data={
                    "category": "linear",
                    "symbol": symbol,
                    "side": side,
                    "orderType": "Market",
                    "qty": position["size"],
                    "reduceOnly": True
                }
            )
            
            return {
                "success": True,
                "result": response
            }
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type
                },
                log_message="Failed to close position"
            )
            raise ExchangeError(
                "Failed to close position",
                context={
                    "symbol": symbol,
                    "exchange": self.exchange_type,
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
    ) -> Dict:
        """
        Place signal order with error handling.

        Args:
            symbol: Trading symbol
            side: Order side (buy/sell)
            size: Order size
            client_id: Client order ID
            leverage: Leverage to use
            take_profit: Optional take profit price

        Returns:
            Dict: Order placement results

        Raises:
            OrderException: If order placement fails
            ValidationError: If parameters are invalid
        """
        try:
            # Validate inputs
            if float(size) <= 0:
                raise ValidationError(
                    "Size must be positive",
                    context={
                        "size": size,
                        "symbol": symbol
                    }
                )

            position_status = await self.handle_current_position(symbol, side, leverage)
            if position_status.get("action_needed"):
                raise OrderException(
                    "Position handling failed",
                    context={
                        "symbol": symbol,
                        "status": position_status
                    }
                )

            # Calculate order parameters
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side.lower() == "buy" else prices["ask_price"]

            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": side.capitalize(),
                "orderType": "Limit",
                "qty": size,
                "price": str(entry_price),
                "orderLinkId": client_id,
            }

            if take_profit:
                symbol_info = await self.get_symbol_info(symbol)
                tick_size = symbol_info["tick_size"]
                
                validated_tp = await self.validate_price(symbol, side, "take_profit", Decimal(take_profit))
                
                multiplier = Decimal("-9") if side.lower() == "buy" else Decimal("9")
                tp_trigger = validated_tp + (tick_size * multiplier)
                
                order_params.update({
                    "takeProfit": str(validated_tp),
                    "tpTriggerBy": "LastPrice",
                    "tpslMode": "Partial",
                    "tpOrderType": "Limit",
                    "tpLimitPrice": str(tp_trigger)
                })

            order_result = await self._execute_request(
                method="POST",
                endpoint="/v5/order/create",
                data=order_params
            )

            if order_result:
                order_id = order_result.get("orderId")
                if order_id:
                    monitor_result = await self.order_monitor(symbol, order_id)
                    order_result["monitor_status"] = monitor_result

            return {
                "success": True,
                "order": order_result,
                "position_status": position_status
            }

        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "exchange": self.exchange_type,
                    "client_id": client_id
                },
                log_message="Failed to place signal order"
            )
            raise ExchangeError(
                "Failed to place signal order",
                context={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "exchange": self.exchange_type,
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
    ) -> Dict:
        """
        Place ladder order with error handling.

        Args:
            symbol: Trading symbol
            side: Order side (buy/sell)
            size: Order size
            client_id: Client order ID
            take_profit: Take profit price
            leverage: Leverage to use

        Returns:
            Dict: Order placement results

        Raises:
            OrderException: If order placement fails
            ValidationError: If parameters are invalid
        """
        try:
            # Handle current position
            position_status = await self.handle_current_position(symbol, side, leverage)
            if position_status.get("action_needed"):
                raise OrderException(
                    "Position handling failed",
                    context={
                        "symbol": symbol,
                        "status": position_status
                    }
                )

            # Cancel existing orders
            await self.cancel_all_orders(symbol)
            if not cancel_result["success"]:
                raise ExchangeError(
                    "Failed to cancel existing orders",
                    context={
                        "symbol": symbol,
                        "result": cancel_result
                    }
                )
            
            # Calculate order parameters
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side.lower() == "buy" else prices["ask_price"]

            # Validate take profit
            try:
                validated_tp = await self.validate_price(
                    symbol,
                    side,
                    "take_profit",
                    Decimal(take_profit)
                )
            except (DecimalException, ValidationError) as e:
                raise ValidationError(
                    "Invalid take profit value",
                    context={
                        "take_profit": take_profit,
                        "error": str(e)
                    }
                )

            # Place main order
            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": side.capitalize(),
                "orderType": "Limit",
                "qty": size,
                "price": str(entry_price),
                "orderLinkId": client_id,
                "takeProfit": str(validated_tp),
                "tpTriggerBy": "LastPrice",
                "tpslMode": "Full",
                "tpOrderType": "Market"
            }

            order_result = await self._execute_request(
                method="POST",
                endpoint="/v5/order/create",
                data=order_params
            )

            if order_result:
                order_id = order_result.get("orderId")
                if order_id:
                    monitor_result = await self.order_monitor(symbol, order_id)
                    order_result["monitor_status"] = monitor_result

            return {
                "success": True,
                "order": order_result,
                "position_status": position_status
            }

        except (ValidationError, OrderException):
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "exchange": self.exchange_type,
                    "client_id": client_id,
                    "take_profit": take_profit
                },
                log_message="Failed to place ladder order"
            )
            raise ExchangeError(
                "Failed to place ladder order",
                context={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def position_control(self, symbol: str, order_type: str) -> Dict:
        """
        Control position with error handling.

        Args:
            symbol: Trading symbol
            order_type: Type of control action

        Returns:
            Dict: Control operation results

        Raises:
            ExchangeError: If control operation fails
        """
        try:
            # Cancel all orders
            cancel_result = await self.cancel_all_orders(symbol)
            if not cancel_result["success"]:
                await handle_api_error(
                    error=ExchangeError(
                        "Failed to cancel orders",
                        context={
                            "symbol": symbol,
                            "result": cancel_result
                        }
                    ),
                    context={
                        "symbol": symbol,
                        "cancel_result": cancel_result
                    },
                    log_message="Order cancellation failed during position control"
                )
                raise ExchangeError(
                    "Failed to cancel orders",
                    context={
                        "symbol": symbol,
                        "result": cancel_result
                    }
                )
            
            # Close position
            close_result = await self.close_position(symbol)
            if not close_result["success"]:
                await handle_api_error(
                    error=ExchangeError(
                        "Failed to close position",
                        context={
                            "symbol": symbol,
                            "result": close_result
                        }
                    ),
                    context={
                        "symbol": symbol,
                        "close_result": close_result
                    },
                    log_message="Position closure failed during position control"
                )
                raise ExchangeError(
                    "Failed to close position",
                    context={
                        "symbol": symbol,
                        "result": close_result
                    }
                )

            # Set position mode
            await self.set_position_mode()

            return {
                "success": True,
                "cancel_result": cancel_result,
                "close_result": close_result
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "symbol": symbol,
                    "order_type": order_type,
                    "exchange": self.exchange_type
                },
                log_message="Position control failed"
            )
            raise ExchangeError(
                "Position control failed",
                context={
                    "symbol": symbol,
                    "order_type": order_type,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

# Move imports to end to avoid circular dependencies
from app.core.errors import (
    ValidationError,
    ExchangeError,
    RequestException,
    RateLimitError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.services.exchange.base import BaseExchange, ExchangeCredentials
from app.services.websocket.manager import ws_manager  # For WebSocket integration
from app.core.references import ExchangeType

logger = get_logger(__name__)