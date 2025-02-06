"""
Bitget exchange implementation with enhanced service integration.

Features:
- Trading service integration
- WebSocket manager integration
- Enhanced error handling
- Improved logging
- Reference validation
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
import hmac
import base64
import hashlib
import json
import asyncio
from datetime import datetime

class BitgetExchange(BaseExchange):
    """
    Bitget-specific exchange implementation with service integration.
    
    Features:
    - Trading service integration
    - WebSocket manager integration
    - Enhanced error handling 
    - Proper logging
    - Reference validation
    """
    
    def __init__(self, credentials: ExchangeCredentials):
        """Initialize BitgetExchange."""
        try:
            super().__init__(credentials)
            self.exchange_type = ExchangeType.Bitget
            self.product_type = "SUSDT-FUTURES" if credentials.testnet else "USDT-FUTURES"
            self.margin_coin = "SUSDT" if credentials.testnet else "USDT"
            self._rate_limit = 20
            self.logger = logger.getChild(f"bitget_exchange")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"credentials": credentials.__dict__},
                log_message="Failed to initialize Bitget exchange"
            )
            raise

    def _get_base_url(self) -> str:
        """Get Bitget API base URL."""
        return "https://api.bitget.com"
        
    async def _sign_request(
        self,
        timestamp: str,
        method: str,
        endpoint: str,
        body: str = ""
    ) -> Dict[str, str]:
        """Sign Bitget API request."""
        try:
            message = timestamp + method.upper() + endpoint + (body if body else "")
            
            signature = base64.b64encode(
                hmac.new(
                    self.credentials.api_secret.encode("utf-8"),
                    message.encode("utf-8"),
                    hashlib.sha256
                ).digest()
            ).decode("utf-8")

            return {
                "ACCESS-KEY": self.credentials.api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.credentials.passphrase,
                "X-CHANNEL-API-CODE": "1", 
                "Content-Type": "application/json"
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
        """Execute HTTP request to Bitget API."""
        if self.session is None:
            await self.connect()

        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(datetime.now().timestamp() * 1000))
        body = json.dumps(data) if method == "POST" and data else ""
        headers = await self._sign_request(timestamp, method, endpoint, body)

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
                    
                    if result.get("code") != "00000":
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
                    "exchange": self.exchange_type,
                    "method": method,
                    "endpoint": endpoint,
                    "data": data,
                    "params": params
                },
                log_message="Request execution failed"
            )
            raise ExchangeError(
                "Request failed",
                context={
                    "exchange": self.exchange_type,
                    "method": method,
                    "endpoint": endpoint,
                    "error": str(e)
                }
            )

    async def _fetch_symbol_info_from_exchange(
        self,
        symbol: str
    ) -> Dict[str, Decimal]:
        """Fetch symbol specifications from Bitget."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/market/instruments-info",  
                params={
                    "symbol": symbol,
                    "productType": self.product_type
                }
            )
            
            if not response.get("list"):
                raise ExchangeError(
                    "Symbol not found",
                    context={
                        "exchange": self.exchange_type,
                        "symbol": symbol
                    }
                )
                
            instrument = response["list"][0]
            
            try:
                tick_size = Decimal(instrument["priceEndStep"]) / (10 ** Decimal(instrument["pricePlace"]))
                lot_size = Decimal(instrument["sizeMultiplier"]) 
                contract_size = Decimal("1")
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "exchange": self.exchange_type,
                        "symbol": symbol,
                        "instrument_data": instrument
                    },
                    log_message="Invalid symbol specifications"
                )
                raise ValidationError(
                    "Invalid symbol specifications",
                    context={
                        "exchange": self.exchange_type,
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
                    "error": str(e)
                }
            )

    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        """Get current price information for a symbol."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/market/ticker",
                params={
                    "symbol": symbol,
                    "productType": self.product_type
                }
            )
            
            if not response.get("list"):
                raise ExchangeError(
                    "No price data available",
                    context={
                        "symbol": symbol,
                        "exchange": self.exchange_type
                    }
                )
                
            data = response["list"][0]
            
            try:
                return {
                    "last_price": Decimal(data["lastPr"]),
                    "bid_price": Decimal(data["bidPr"]),
                    "ask_price": Decimal(data["askPr"])
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
        """Get account balance information."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/account/account",
                params={
                    "productType": self.product_type,
                    "marginCoin": self.margin_coin
                }
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
                    "balance": Decimal(data["available"]),
                    "equity": Decimal(data["accountEquity"])
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
        """Get all open positions."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/position/all-position",
                params={
                    "productType": self.product_type,
                    "marginCoin": self.margin_coin
                }
            )
            
            positions = response if isinstance(response, list) else []
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
        """Get current position for a specific symbol."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/position/single-position",
                params={
                    "symbol": symbol,
                    "marginCoin": self.margin_coin,
                    "productType": self.product_type
                }
            )
            
            positions = response if isinstance(response, list) else []
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
                "productType": self.product_type,
                "marginCoin": self.margin_coin,
                "startTime": int(start_time.timestamp() * 1000),
                "endTime": int(end_time.timestamp() * 1000)
            }
            if symbol:
                params["symbol"] = symbol
                
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/position/history-position",
                params=params
            )
            
            positions = []
            for pos in response.get("list", []):
                try:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": pos["holdSide"],
                        "entry_price": Decimal(pos["openAvgPrice"]),
                        "exit_price": Decimal(pos["closeAvgPrice"]),
                        "size": Decimal(pos["openTotalPos"]),
                        "raw_pnl": Decimal(pos["pnl"]),
                        "trading_fee": -(Decimal(pos.get("openFee", "0")) + Decimal(pos.get("closeFee", "0"))),
                        "funding_fee": Decimal(pos.get("totalFunding", "0")),
                        "net_pnl": Decimal(pos["netProfit"]),
                        "pnl_ratio": Decimal(pos["pnl"]) / (Decimal(pos["openTotalPos"]) * Decimal(pos["openAvgPrice"])) * Decimal("100"),
                        "opened_at": datetime.fromtimestamp(int(pos["cTime"]) / 1000),
                        "closed_at": datetime.fromtimestamp(int(pos["uTime"]) / 1000)
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
        """Get position side (buy/sell)."""
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            if "holdSide" not in position:
                raise ValidationError(
                    "Missing position side data",
                    context={"position_data": position}
                )
            return "buy" if position["holdSide"].lower() == "long" else "sell"
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
        """Check if position is empty."""
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

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get the status of a specific order."""
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v2/mix/order/detail",
                params={
                    "symbol": symbol,
                    "orderId": order_id,
                    "productType": self.product_type
                }
            )
            return response if response else None
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
        """Amend the price of an existing order."""
        try:
            if new_price <= 0:
                raise ValidationError(
                    "Price must be positive",
                    context={
                        "price": str(new_price),
                        "symbol": symbol
                    }
                )

            validated_price = await self.validate_price(
                symbol=symbol,
                side="buy",  # Side doesn't matter for validation
                price_type="limit",
                price=new_price
            )

            return await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/modify-order",
                data={
                    "orderId": order_id,
                    "symbol": symbol,
                    "productType": self.product_type,
                    "newClientOid": order_id,
                    "newPrice": str(validated_price)
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
        """Set leverage for both buy and sell sides."""
        try:
            # Set margin mode first
            await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/account/set-margin-mode",
                data={
                    "symbol": symbol,
                    "productType": self.product_type,
                    "marginCoin": self.margin_coin,
                    "marginMode": "crossed"
                }
            )
            
            # Then set leverage
            return await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/account/set-leverage",
                data={
                    "symbol": symbol,
                    "productType": self.product_type,
                    "marginCoin": self.margin_coin,
                    "leverage": leverage
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
        """Set position mode to one-way."""
        try:
            return await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/account/set-position-mode",
                data={
                    "productType": self.product_type,
                    "posMode": "one_way_mode"
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
        """Cancel all orders, optionally filtered by symbol."""
        try:
            data = {
                "productType": self.product_type,
                "marginCoin": self.margin_coin
            }
            if symbol:
                data["symbol"] = symbol

            # Cancel regular orders
            await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/cancel-all-orders",
                data=data
            )

            # Cancel algo/plan orders
            await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/cancel-plan-order",
                data={
                    **data,
                    "planType": "normal_plan"
                }
            )
            
            return {"success": True}
                
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
        """Close an open position for a symbol."""
        try:
            response = await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/close-positions",
                data={
                    "symbol": symbol,
                    "productType": self.product_type
                }
            )
            
            return {"success": True, "result": response}
            
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
        """Place a signal order."""
        try:
            position_status = await self.handle_current_position(symbol, side, leverage)
            if position_status.get("action_needed"):
                raise ExchangeError(
                    "Position handling failed",
                    context={
                        "symbol": symbol,
                        "position_status": position_status
                    }
                )

            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]

            order_params = {
                "symbol": symbol,
                "productType": self.product_type,
                "marginMode": "crossed",
                "marginCoin": self.margin_coin,
                "size": size,
                "price": str(entry_price),
                "side": side,
                "orderType": "limit",
                "clientOid": client_id
            }

            if take_profit:
                try:
                    validated_tp = await self.validate_price(symbol, side, "take_profit", Decimal(take_profit))
                    order_params["presetStopSurplusPrice"] = validated_tp
                except DecimalException as e:
                    raise ValidationError(
                        "Invalid take profit value",
                        context={
                            "take_profit": take_profit,
                            "error": str(e)
                        }
                    )

            order_result = await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/place-order",
                data=order_params
            )

            if order_result:
                order_id = order_result.get("ordId")
                if not order_id:
                    raise ValidationError(
                        "Missing order ID in response",
                        context={"order_result": order_result}
                    )
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
        """Place a ladder order."""
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
                        "symbol": symbol,
                        "position_status": position_status
                    }
                )

            # Cancel existing orders
            await self.cancel_all_orders(symbol)

            # Get price
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]

            try:
                validated_tp = await self.validate_price(symbol, side, "take_profit", Decimal(take_profit))
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
                "symbol": symbol,
                "productType": self.product_type,
                "marginMode": "crossed",
                "marginCoin": self.margin_coin,
                "size": size,
                "price": str(entry_price),
                "side": side,
                "orderType": "limit",
                "clientOid": client_id,
                "presetStopSurplusPrice": str(validated_tp)
            }

            order_result = await self._execute_request(
                method="POST",
                endpoint="/api/v2/mix/order/place-order",
                data=order_params
            )

            if order_result:
                order_id = order_result.get("ordId")
                if not order_id:
                    raise ValidationError(
                        "Missing order ID in response",
                        context={"order_result": order_result}
                    )
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
        """Control existing position."""
        try:
            # Cancel all orders for the symbol
            cancel_result = await self.cancel_all_orders(symbol)
            if not cancel_result["success"]:
                raise ExchangeError(
                    "Failed to cancel orders",
                    context={
                        "symbol": symbol,
                        "cancel_result": cancel_result
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
                            "close_result": close_result
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
                        "close_result": close_result
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