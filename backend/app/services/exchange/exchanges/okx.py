"""
This module provides the OKXExchange class, a concrete subclass of BaseExchange, for integrating with OKX"s REST API.

OKXExchange:
- Selects the appropriate base URL for OKX (mainnet).
- Implements authentication using HMAC with API key, secret, and passphrase.
- Provides methods to fetch symbol info, current prices, account balances, positions, and manage orders.
- Handles rate limits, errors, and supports operations like placing signals and ladder orders.
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
import hmac
import base64
import hashlib
import json
import asyncio
from datetime import datetime

class OKXExchange(BaseExchange):
    """
    OKX-specific exchange client extending BaseExchange.

    Behavior:
    - Sets exchange_type to OKX.
    - Uses "https://www.okx.com" as the base URL.
    - Authenticates requests with HMAC-SHA256 using api_key, api_secret, passphrase, and a timestamp.
    - Implements required abstract methods for market data, positions, and orders using OKX API endpoints.

    Attributes:
        exchange_type (ExchangeType): Set to ExchangeType.OKX.
        _rate_limit (int): Adjusted rate limit for requests.
    """

    def __init__(self, credentials: ExchangeCredentials):
        """
        Initialize OKXExchange with given credentials.

        Args:
            credentials (ExchangeCredentials): Includes api_key, api_secret, passphrase, and testnet flag.

        Side Effects:
            - Sets exchange_type to OKX.
            - Adjusts rate_limit.
            - Validates credentials.
            
        Raises:
            ExchangeError: If credentials are invalid.
        """
        try:
            super().__init__(credentials)
            self.exchange_type = ExchangeType.OKX
            self._rate_limit = 20
            self.logger = logger.getChild(f"okx_exchange")
            
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"credentials": credentials.__dict__},
                log_message="Failed to initialize OKX exchange"
            )
            raise


    def _get_base_url(self) -> str:
        """Get the OKX base URL."""
        return "https://www.okx.com"

    async def _sign_request(self, method: str, endpoint: str, data: Dict) -> Dict[str, str]:
        """
        Sign OKX requests using API key, secret, and passphrase.

        Args:
            method (str): HTTP method (GET, POST, etc.).
            endpoint (str): API endpoint path.
            data (Dict): Request body for POST requests, empty for GET.

        Returns:
            Dict[str, str]: Headers including OK-ACCESS-KEY, OK-ACCESS-SIGN, etc.

        Behavior:
            - Constructs a message with timestamp, method, endpoint, and body.
            - Signs with HMAC-SHA256 and base64 encodes the signature.
            - Adds "x-simulated-trading" header if testnet is true.
            
        Raises:
            ExchangeError: If signing fails.
        """
        try:
            timestamp = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

            # Construct message
            message = f"{timestamp}{method.upper()}{endpoint}"
            if data:
                message += json.dumps(data)

            # Generate signature
            signature = base64.b64encode(
                hmac.new(
                    self.credentials.api_secret.encode("utf-8"),
                    message.encode("utf-8"),
                    hashlib.sha256
                ).digest()
            ).decode("utf-8")

            # Create headers
            headers = {
                "OK-ACCESS-KEY": self.credentials.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
                "Content-Type": "application/json"
            }

            # Add testnet header if needed
            if self.credentials.testnet:
                headers["x-simulated-trading"] = "1"

            return headers

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "method": method,
                    "endpoint": endpoint,
                    "exchange": self.exchange_type
                },
                log_message="Failed to sign request"
            )
            raise ExchangeError(
                "Failed to sign request",
                context={
                    "method": method,
                    "endpoint": endpoint,
                    "exchange": self.exchange_type,
                    "error": str(e)
                }
            )

    async def _execute_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict:
        """
        Execute an HTTP request against the OKX API.

        Args:
            method (str): HTTP method.
            endpoint (str): The API endpoint.
            data (Dict, optional): JSON data for POST requests.
            params (Dict, optional): Query parameters for GET requests.

        Returns:
            Dict: The "result" field of the successful JSON response.

        Raises:
            RequestException: If "code" in response is not "0".
            ExchangeException: On other errors handled by _handle_error.
        """
        if self.session is None:
            await self.connect()

        url = f"{self.base_url}{endpoint}"
        headers = await self._sign_request(method=method, endpoint=endpoint, data=data or {})

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

                    if result.get("code") != "0":
                        await handle_api_error(
                            error=RequestException(
                                result.get("msg", "Unknown error"),
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
                            result.get("msg", "Unknown error"),
                            context={
                                "response": result,
                                "endpoint": endpoint,
                                "exchange": self.exchange_type
                            }
                        )
                    
                    return result.get("data", {})

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
        Fetch symbol specifications (tickSz, lotSz, ctVal) from OKX.

        Endpoint:
            /api/v5/public/instruments?instType=SWAP&instId=...

        Args:
            symbol (str): Trading symbol.

        Returns:
            Dict[str, Decimal]: tick_size, lot_size, contract_size.

        Raises:
            ExchangeException: If symbol not found or request fails.
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/public/instruments",
                params={
                    "instType": "SWAP",
                    "instId": symbol
                }
            )

            if not response:
                raise ExchangeError(
                    "Symbol not found",
                    context={
                        "symbol": symbol,
                        "exchange": self.exchange_type
                    }
                )

            instrument = response[0]
            return {
                "tick_size": Decimal(instrument["tickSz"]),
                "lot_size": Decimal(instrument["lotSz"]),
                "contract_size": Decimal(instrument["ctVal"])
            }

        except (KeyError, DecimalException) as e:
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
        Get current price data for a symbol.

        Endpoint:
            /api/v5/market/ticker?instId=...

        Args:
            symbol (str): Trading symbol.

        Returns:
            Dict[str, Decimal]: last_price, bid_price, ask_price.

        Raises:
            ExchangeException: If no price data found or request fails.
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/market/ticker",
                params={"instId": symbol}
            )

            if not response:
                raise ExchangeError(
                    "No price data found",
                    context={
                        "symbol": symbol,
                        "exchange": self.exchange_type
                    }
                )

            data = response[0]
            return {
                "last_price": Decimal(data["last"]),
                "bid_price": Decimal(data["bidPx"]),
                "ask_price": Decimal(data["askPx"])
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
        Get account balance for a given currency.

        Endpoint:
            /api/v5/account/balance?ccy=...

        Args:
            currency (str): Currency code, default "USDT".

        Returns:
            Dict[str, Decimal]: 
            - "balance": Available balance
            - "equity": Total equity value
            - "accountMode": Account Mode

        Raises:
            ExchangeError: If balance retrieval fails
            RequestException: If API request fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/account/balance",
                params={"ccy": currency}
            )
            
            if not response.get("data"):
                raise ExchangeError(
                    "No balance data available",
                    context={
                        "currency": currency,
                        "exchange": self.exchange_type
                    }
                )
            
            data = response["data"][0]
            details = data.get("details", [{}])[0]
            
            try:
                return {
                    "balance": Decimal(details["availEq"]),
                    "equity": Decimal(data["totalEq"])
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
        Get all open positions across all symbols.

        Endpoint:
            /api/v5/account/positions?instType=SWAP

        Returns:
            List[Dict]: Positions with nonzero size.

        Raises:
            ExchangeError: If position retrieval fails
            RequestException: If API request fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/account/positions",
                params={"instType": "SWAP"}
            )
            
            positions = response.get("data", []) if isinstance(response, dict) else (response or [])
            return [pos for pos in positions if Decimal(pos.get("pos", "0")) != 0]
            
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
        Get current position for a specific symbol.

        Endpoint:
            /api/v5/account/positions?instId=...

        Args:
            symbol (str): Trading symbol.

        Returns:
            Optional[Dict]: Position details or None if no position.

        Raises:
            ExchangeError: If position retrieval fails
            RequestException: If API request fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/account/positions",
                params={
                    "instType": "SWAP",
                    "instId": symbol
                }
            )
            
            positions = response.get("data", []) if isinstance(response, dict) else (response or [])
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
        """
        Get closed position history.

        Endpoint:
            /api/v5/account/positions-history

        Args:
            start_time: Start of time range
            end_time: End of time range
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Historical position data.

        Raises:
            ExchangeError: If history retrieval fails
            RequestException: If API request fails
        """
        try:
            params = {
                "instType": "SWAP",
                "after": str(int(end_time.timestamp() * 1000)),
                "before": str(int(start_time.timestamp() * 1000)),
                "limit": "100",              
            }
            if symbol:
                params["instId"] = symbol

            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/account/positions-history",
                params=params
            )

            positions = []
            for pos in response:
                try:
                    # Convert exchange format to standardized format
                    direction = pos["direction"] if pos["posSide"] == "net" else pos["posSide"]
                    
                    positions.append({
                        "symbol": pos["instId"],
                        "side": direction,
                        "entry_price": Decimal(pos["openAvgPx"]),
                        "exit_price": Decimal(pos["closeAvgPx"]),
                        "size": Decimal(pos["closeTotalPos"]),
                        "raw_pnl": Decimal(pos["pnl"]),
                        "trading_fee": Decimal(pos["fee"]),
                        "funding_fee": Decimal(pos["fundingFee"]),
                        "net_pnl": Decimal(pos["realizedPnl"]), 
                        "pnl_ratio": Decimal(pos["pnlRatio"]) * Decimal("100"),
                        "opened_at": datetime.fromtimestamp(int(pos["cTime"]) / 1000),
                        "closed_at": datetime.fromtimestamp(int(pos["uTime"]) / 1000)
                    })

                except (KeyError, DecimalException) as e:
                    await handle_api_error(
                        error=e,
                        context={
                            "position": pos,
                            "exchange": self.exchange_type
                        },
                        log_message="Failed to process position"
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
        Get position side (buy/sell) based on instrument type and position data.
        
        For non-MARGIN types:
            - Positive pos = long position
            - Negative pos = short position
        For MARGIN type:
            - Position side determined by posCcy vs base currency (first part of instId)
        """
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )

            inst_type = position.get("instType")
            
            if inst_type == "MARGIN":
                # For margin, check if posCcy matches base currency
                inst_id = position.get("instId", "")
                base_currency = inst_id.split("-")[0] if "-" in inst_id else ""
                pos_currency = position.get("posCcy", "")
                
                return "buy" if pos_currency == base_currency else "sell"
            else:
                # For other types, check pos value
                pos = Decimal(position.get("pos", "0"))
                return "buy" if pos > 0 else "sell"
                
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
        Check if position is empty (size = 0).

        Args:
            position (Dict): Position details from OKX.

        Returns:
            bool: True if position size is 0.

        Raises:
            ValidationError: If position format is invalid
        """
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            return Decimal(position.get("pos", "0")) == Decimal("0")
            
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

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """
        Get the status of a specific order.

        Endpoint:
            /api/v5/trade/order?instId=...&ordId=...

        Args:
            symbol: Trading symbol
            order_id: OKX order ID

        Returns:
            Optional[Dict]: Order details if found, else None

        Raises:
            OrderException: If order status check fails
            RequestException: If API request fails
        """
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/trade/order",
                params={
                    "instId": symbol,
                    "ordId": order_id
                }
            )

            order = response[0] if response else None
            
            if order:
                self.logger.debug(
                    "Retrieved order status",
                    extra={
                        "symbol": symbol,
                        "order_id": order_id,
                        "status": order.get("state")
                    }
                )

            return order

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
        Amend an existing order"s price.

        Endpoint:
            /api/v5/trade/amend-order

        Args:
            symbol: Trading symbol
            order_id: Order to amend
            new_price: New order price

        Returns:
            Dict: Result of the amend request

        Raises:
            OrderException: If order amendment fails
            ValidationError: If price is invalid
            RequestException: If API request fails
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

            return await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/amend-order",
                data={
                    "instId": symbol,
                    "ordId": order_id,
                    "newPx": str(validated_price)
                }
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
        Set leverage for a symbol in cross margin mode.

        Endpoint:
            /api/v5/account/set-leverage

        Args:
            symbol: Trading symbol
            leverage: Desired leverage value

        Returns:
            Dict: Result of leverage change

        Raises:
            ExchangeError: If leverage setting fails
            ValidationError: If leverage is invalid
            RequestException: If API request fails
        """
        try:
            leverage_val = int(leverage)
            if leverage_val <= 0 or leverage_val > 100:
                raise ValidationError(
                    "Invalid leverage value",
                    context={
                        "leverage": leverage,
                        "valid_range": "1-100"
                    }
                )

            # Set margin mode first
            await self._execute_request(
                method="POST",
                endpoint="/api/v5/account/set-leverage",
                data={
                    "instId": symbol,
                    "lever": leverage,
                    "mgnMode": "cross"
                }
            )

            return {
                "success": True,
                "leverage": leverage_val,
                "symbol": symbol
            }
            
        except ValidationError:
            raise
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
        Set position mode to net mode.

        Endpoint:
            /api/v5/account/set-position-mode

        Returns:
            Dict: Result of mode change request

        Raises:
            ExchangeError: If mode setting fails
            RequestException: If API request fails
        """
        try:
            return await self._execute_request(
                method="POST",
                endpoint="/api/v5/account/set-position-mode",
                data={"posMode": "net_mode"}
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
        Cancel all pending orders, optionally filtered by symbol.

        Endpoint:
            /api/v5/trade/cancel-batch-orders
            /api/v5/trade/cancel-algos

        Args:
            symbol: Optional symbol filter

        Returns:
            Dict: {"success": True/False, "result": ...}

        Raises:
            OrderException: If order cancellation fails
            RequestException: If API request fails
        """
        try:
            data = {"instType": "SWAP"}
            if symbol:
                data["instId"] = symbol

            # Cancel regular orders
            regular_orders = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/cancel-batch-orders",
                data=data
            )

            # Cancel algo orders
            algo_orders = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/cancel-algos",
                data=data
            )

            return {
                "success": True,
                "regular_orders": regular_orders,
                "algo_orders": algo_orders
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
        Close an open position for a symbol using a market order.

        Endpoint:
            /api/v5/trade/close-position

        Args:
            symbol: Trading symbol

        Returns:
            Dict: {"success": True, "result": ...}

        Raises:
            PositionException: If position closing fails
            RequestException: If API request fails
        """
        try:
            response = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/close-position",
                data={
                    "instId": symbol,
                    "mgnMode": "cross",
                    "autoCxl": True  # Auto-cancel pending orders
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
        Place a signal order (limit order) after handling current position.

        Steps:
        1. handle_current_position to ensure correct leverage and no conflicting position.
        2. Calculate order size and get best available price.
        3. Place limit order via /api/v5/trade/order.
        4. If take_profit provided, add TP params.
        5. Monitor order with order_monitor.

        Args:
            symbol: Trading symbol
            side: Order side (buy/sell)
            size: Position size
            client_id: Client order ID
            leverage: Desired leverage
            take_profit: Optional take profit price

        Returns:
            Dict: {"success": True/False, "order": ..., "monitor_status": ...}

        Raises:
            OrderException: If order placement fails
            ValidationError: If parameters are invalid
            ExchangeError: If position handling fails
            RequestException: If API request fails
        """
        try:
            # Handle current position
            position_status = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            if position_status.get("action_needed"):
                raise ExchangeError(
                    "Position handling failed",
                    context={
                        "status": position_status,
                        "symbol": symbol,
                        "side": side
                    }
                )

            # Calculate size and get entry price
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]

            # Prepare order parameters
            order_params = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side,
                "ordType": "limit",
                "sz": size,
                "px": str(entry_price),
                "clOrdId": client_id
            }

            # Add take profit if specified
            if take_profit:
                try:
                    validated_tp = await self.validate_price(
                        symbol=symbol,
                        side=side,
                        price_type="take_profit",
                        price=Decimal(take_profit)
                    )
                    order_params.update({
                        "tpTriggerPx": str(validated_tp),
                        "tpOrdPx": str(validated_tp)
                    })
                except DecimalException as e:
                    raise ValidationError(
                        "Invalid take profit value",
                        context={
                            "take_profit": take_profit,
                            "error": str(e)
                        }
                    )

            # Place order
            order_result = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/order",
                data=order_params
            )

            # Monitor order if successful
            if order_result:
                order_id = order_result[0].get("ordId")
                if order_id:
                    monitor_result = await self.order_monitor(symbol, order_id)
                    order_result[0]["monitor_status"] = monitor_result

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
                    "exchange": self.exchange_type
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
        Place a ladder order with take profit.

        Steps:
        1. handle_current_position
        2. cancel_all_orders for symbol
        3. place main limit order at best price
        4. add take profit as algo order
        5. monitor order

        Args:
            symbol: Trading symbol
            side: Order side (buy/sell)
            size: Position size
            client_id: Client order ID
            take_profit: Take profit price
            leverage: Desired leverage

        Returns:
            Dict: {"success": True/False, "order": ..., "position_status": ...}

        Raises:
            OrderException: If order placement fails
            ValidationError: If parameters are invalid
            ExchangeError: If position handling fails
            RequestException: If API request fails
        """
        try:
            # Handle current position
            position_status = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            if position_status.get("action_needed"):
                raise ExchangeError(
                    "Position handling failed",
                    context={
                        "status": position_status,
                        "symbol": symbol,
                        "side": side
                    }
                )

            # Cancel existing orders
            await self.cancel_all_orders(symbol)

            # Get entry price
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]

            try:
                validated_tp = await self.validate_price(
                    symbol=symbol,
                    side=side,
                    price_type="take_profit",
                    price=Decimal(take_profit)
                )
            except (DecimalException, ValidationError) as e:
                raise ValidationError(
                    "Invalid take profit value",
                    context={
                        "take_profit": take_profit,
                        "error": str(e)
                    }
                )

            # Place main order with take profit
            order_params = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side,
                "ordType": "limit",
                "sz": size,
                "px": str(entry_price),
                "clOrdId": client_id,
                "tpTriggerPx": str(validated_tp),
                "tpOrdPx": str(validated_tp)
            }

            order_result = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/order",
                data=order_params
            )

            self.logger.info(
                "Placed ladder order",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "price": str(entry_price),
                    "take_profit": str(validated_tp),
                    "client_id": client_id
                }
            )

            # Monitor order if successful
            if order_result:
                order_id = order_result[0]["ordId"]
                monitor_result = await self.order_monitor(symbol, order_id)
                order_result[0]["monitor_status"] = monitor_result

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
                    "exchange": self.exchange_type
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

    async def position_control(
        self,
        symbol: str,
        order_type: str
    ) -> Dict:
        """
        Control position by cancelling orders and closing position.

        Steps:
        1. Cancel all orders for symbol
        2. Close the position
        3. Set position mode

        Args:
            symbol: Trading symbol
            order_type: Type of control action

        Returns:
            Dict: {"success": True, "cancel_result": ..., "close_result": ...}

        Raises:
            PositionException: If position control fails
            OrderException: If order cancellation fails
            RequestException: If API request fails
        """
        try:
            # Cancel all orders
            cancel_result = await self.cancel_all_orders(symbol)
            if not cancel_result["success"]:
                raise OrderException(
                    "Failed to cancel orders",
                    context={
                        "symbol": symbol,
                        "result": cancel_result
                    }
                )

            # Close position
            close_result = await self.close_position(symbol)
            if not close_result["success"]:
                raise PositionException(
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