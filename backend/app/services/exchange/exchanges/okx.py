"""
This module provides the OKXExchange class, a concrete subclass of BaseExchange,
for integrating with OKX's REST API.

OKXExchange:
- Uses "https://www.okx.com" as the base URL.
- Authenticates requests with HMAC-SHA256 using API key, secret, passphrase, and a timestamp.
- Implements methods for fetching symbol info, current prices, balances, positions, and managing orders.
- Handles rate limits, errors, and supports operations like placing signals and ladder orders.
"""

from typing import Dict, List, Optional
from decimal import Decimal, InvalidOperation
import hmac
import base64
import hashlib
import json
import asyncio
from datetime import datetime

from app.services.exchange.base import BaseExchange, ExchangeCredentials
from app.core.errors.decorators import error_handler
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType
from app.core.config.settings import settings
from app.core.errors.base import (
    ValidationError,
    ExchangeError,
    RequestException
)

logger = get_logger(__name__)


class OKXExchange(BaseExchange):
    """
    OKX-specific exchange client extending BaseExchange.

    Attributes:
        exchange_type (ExchangeType): Set to ExchangeType.OKX.
        _rate_limit (int): Adjusted rate limit for requests.
    """

    @error_handler(
        context_extractor=lambda self, credentials: {"api_key": credentials.api_key},
        log_message="Initialization of OKXExchange failed"
    )
    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__(credentials)
        self.exchange_type = ExchangeType.OKX
        self._rate_limit = 20
        self.logger = get_logger("okx_exchange")

    def _get_base_url(self) -> str:
        return "https://www.okx.com"

    @error_handler(
        context_extractor=lambda self, method, endpoint, data, params: {
            "method": method, "endpoint": endpoint, "data": data, "params": params
        },
        log_message="_execute_request failed"
    )
    async def _execute_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict:
        if self.session is None:
            await self.connect()
        url = f"{self.base_url}{endpoint}"
        timestamp = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        headers = await self._sign_request(timestamp, method, endpoint, data)
        await self._handle_rate_limit()
        async with self._request_semaphore:
            async with self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=data if method.upper() == "POST" else None,
                params=params if method.upper() == "GET" else None
            ) as response:
                response.raise_for_status()
                result = await response.json()
                if result.get("code") != "0":
                    exc = RequestException(
                        result.get("msg", "Unknown error"),
                        context={"response": result, "endpoint": endpoint, "exchange": self.exchange_type}
                    )
                    await handle_api_error(
                        error=exc,
                        context={"response": result, "endpoint": endpoint},
                        log_message=f"API request failed: {endpoint}"
                    )
                    raise exc
                return result.get("data", {})

    @error_handler(
        context_extractor=lambda self, method, endpoint, data=None: {
            "method": method, "endpoint": endpoint, "data": data
        },
        log_message="_sign_request failed"
    )
    async def _sign_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None
    ) -> Dict[str, str]:
        try:
            timestamp = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
            message = f"{timestamp}{method.upper()}{endpoint}"
            if data:
                message += json.dumps(data)
            signature = base64.b64encode(
                hmac.new(
                    self.credentials.api_secret.encode("utf-8"),
                    message.encode("utf-8"),
                    hashlib.sha256
                ).digest()
            ).decode("utf-8")
            headers = {
                "OK-ACCESS-KEY": self.credentials.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
                "Content-Type": "application/json"
            }
            if self.credentials.testnet:
                headers["x-simulated-trading"] = "1"
            return headers
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to sign request",
                context={"method": method, "endpoint": endpoint, "exchange": self.exchange_type},
                error_message="Failed to sign request"
            )

    @error_handler(
        context_extractor=lambda self: {"dummy": "okx_rate_limit"},
        log_message="_handle_rate_limit failed"
    )
    async def _handle_rate_limit(self) -> None:
        min_interval = 1.0 / settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND
        import time
        now = time.time()
        last = self.last_request_time if hasattr(self, "last_request_time") else now
        elapsed = now - last
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self.last_request_time = time.time()

    @error_handler(
        context_extractor=lambda self, e: {"error": str(e)},
        log_message="_handle_exception failed"
    )
    async def _handle_exception(
        self,
        e: Exception,
        log_message: str,
        context: Dict,
        error_message: str,
        exception_cls=ExchangeError
    ) -> None:
        await handle_api_error(error=e, context=context, log_message=log_message)
        raise exception_cls(error_message, context={**context, "error": str(e)})

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="_fetch_symbol_info_from_exchange failed"
    )
    async def _fetch_symbol_info_from_exchange(self, symbol: str) -> Dict[str, Decimal]:
        instrument = None
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/public/instruments",
                params={"instType": "SWAP", "instId": symbol}
            )
            if not response:
                raise ExchangeError(
                    "Symbol not found",
                    context={"symbol": symbol, "exchange": self.exchange_type}
                )
            instrument = response["data"][0]
            return {
                "tick_size": Decimal(instrument["tickSz"]),
                "lot_size": Decimal(instrument["lotSz"]),
                "contract_size": Decimal(instrument["ctVal"])
            }
        except (KeyError, InvalidOperation) as e:
            await self._handle_exception(
                e,
                log_message="Invalid symbol specifications",
                context={"symbol": symbol, "instrument_data": instrument, "exchange": self.exchange_type},
                error_message="Invalid symbol specifications",
                exception_cls=ValidationError
            )
        except (ExchangeError, ValidationError):
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to fetch symbol information",
                context={"symbol": symbol, "exchange": self.exchange_type},
                error_message="Failed to fetch symbol information"
            )

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="get_current_price failed"
    )
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        endpoint = "/api/v5/market/ticker"
        params = {"instId": symbol}
        data = await self._execute_request("GET", endpoint, params=params)
        if not data or not isinstance(data, list) or len(data) == 0:
            raise ExchangeError("No price data available", context={"symbol": symbol, "exchange": self.exchange_type})
        ticker = data[0]
        try:
            return {
                "last_price": Decimal(ticker["last"]),
                "bid_price": Decimal(ticker["bidPx"]),
                "ask_price": Decimal(ticker["askPx"])
            }
        except (KeyError, InvalidOperation) as e:
            raise ValidationError("Invalid price data format", context={"ticker": ticker, "error": str(e)}) from e

    @error_handler(
        context_extractor=lambda self, currency="USDT": {"currency": currency},
        log_message="get_balance failed"
    )
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]:
        endpoint = "/api/v5/account/balance"
        params = {"ccy": currency}
        data = await self._execute_request("GET", endpoint, params=params)
        if not data or not data.get("data"):
            raise ExchangeError("No balance data available", context={"currency": currency, "exchange": self.exchange_type})
        data = data["data"][0]
        details = data.get("details", [{}])[0]
        try:
            return {
                "balance": Decimal(details["availBal"]),
                "equity": Decimal(data["totalEq"])
            }
        except (KeyError, InvalidOperation) as e:
            raise ValidationError("Invalid balance data format", context={"balance_info": data, "error": str(e)}) from e

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="get_position failed"
    )
    async def get_position(self, symbol: str) -> Optional[Dict]:
        endpoint = "/api/v5/account/positions"
        params = {"instId": symbol, "instType": "SWAP"}
        data = await self._execute_request("GET", endpoint, params=params)
        positions = data.get("data", []) if isinstance(data, dict) else (data or [])
        return positions[0] if positions else None

    @error_handler(
        context_extractor=lambda self: {"dummy": "get_all_positions"},
        log_message="get_all_positions failed"
    )
    async def get_all_positions(self) -> List[Dict]:
        endpoint = "/api/v5/account/positions"
        params = {"instType": "SWAP"}
        data = await self._execute_request("GET", endpoint, params=params)
        if not data or not isinstance(data, dict):
            return []
        positions = data.get("data", [])
        return [pos for pos in positions if Decimal(pos.get("pos", "0")) != Decimal("0")]

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="get_position_history failed"
    )
    async def get_position_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        try:
            params = {
                "instType": "SWAP",
                "after": str(int(end_time.timestamp() * 1000)),
                "before": str(int(start_time.timestamp() * 1000)),
                "limit": "100",
            }
            if symbol:
                params["instId"] = symbol
            data = await self._execute_request("GET", "/api/v5/account/positions-history", params=params)
            positions = []#["data"]?
            for pos in data:
                try:
                    positions.append({
                        "symbol": pos["instId"],
                        "side": pos["direction"] if pos.get("posSide") == "net" else pos.get("posSide"),
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
                except (KeyError, InvalidOperation) as e:
                    await handle_api_error(
                        error=e,
                        context={"position": pos, "exchange": self.exchange_type},
                        log_message="Failed to process position"
                    )
                    continue
            return positions
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to get position history",
                context={"symbol": symbol, "date_range": f"{start_time} to {end_time}", "exchange": self.exchange_type},
                error_message="Failed to get position history"
            )

    @error_handler(
        context_extractor=lambda self, position: {"position": position},
        log_message="_get_position_side failed"
    )
    async def _get_position_side(self, position: Dict) -> str:
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            inst_type = position.get("instType")
            if inst_type == "MARGIN":
                inst_id = position.get("instId", "")
                base_currency = inst_id.split("-")[0] if "-" in inst_id else ""
                pos_currency = position.get("posCcy", "")
                return "buy" if pos_currency == base_currency else "sell"
            else:
                pos = Decimal(position.get("pos", "0"))
                return "buy" if pos > 0 else "sell"
        except ValidationError:
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to get position side",
                context={"position": position, "exchange": self.exchange_type},
                error_message="Failed to get position side"
            )

    @error_handler(
        context_extractor=lambda self, position: {"position": position},
        log_message="_is_position_empty failed"
    )
    async def _is_position_empty(self, position: Dict) -> bool:
        try:
            if not isinstance(position, dict):
                raise ValidationError(
                    "Invalid position data type",
                    context={"position_type": type(position).__name__}
                )
            return Decimal(position.get("pos", "0")) == Decimal("0")
        except InvalidOperation as e:
            raise ValidationError("Invalid position size value", context={"position": position, "error": str(e)}) from e
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to check position status",
                context={"position": position, "exchange": self.exchange_type},
                error_message="Failed to check position status"
            )

    @error_handler(
        context_extractor=lambda self, symbol, order_id: {"symbol": symbol, "order_id": order_id},
        log_message="get_order_status failed"
    )
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        try:
            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/trade/order",
                params={"instId": symbol, "ordId": order_id}
            )
            order = response["data"][0] if response else None
            if order:
                self.logger.debug(
                    "Retrieved order status",
                    extra={"symbol": symbol, "order_id": order_id, "status": order.get("state")}
                )
            return order
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to get order status",
                context={"symbol": symbol, "order_id": order_id, "exchange": self.exchange_type},
                error_message="Failed to get order status"
            )

    @error_handler(
        context_extractor=lambda self, symbol, order_id, new_price: {
            "symbol": symbol, "order_id": order_id, "new_price": str(new_price)
        },
        log_message="amend_order failed"
    )
    async def amend_order(self, symbol: str, order_id: str, new_price: Decimal) -> Dict:
        try:
            if new_price <= 0:
                raise ValidationError(
                    "Price must be positive",
                    context={"price": str(new_price), "symbol": symbol}
                )
            validated_price = await self.validate_price(
                symbol=symbol,
                side="buy",
                price_type="limit",
                price=new_price
            )
            return await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/amend-order",
                data={"instId": symbol, "ordId": order_id, "newPx": str(validated_price)}
            )
        except ValidationError:
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to amend order",
                context={"symbol": symbol, "order_id": order_id, "new_price": str(new_price), "exchange": self.exchange_type},
                error_message="Failed to amend order"
            )

    @error_handler(
        context_extractor=lambda self, symbol, leverage: {"symbol": symbol, "leverage": leverage},
        log_message="set_leverage failed"
    )
    async def set_leverage(self, symbol: str, leverage: str) -> Dict:
        try:
            leverage_val = int(leverage)
            if leverage_val <= 0 or leverage_val > 100:
                raise ValidationError(
                    "Invalid leverage value",
                    context={"leverage": leverage, "valid_range": "1-100"}
                )
            await self._execute_request(
                method="POST",
                endpoint="/api/v5/account/set-leverage",
                data={"instId": symbol, "lever": leverage, "mgnMode": "cross"}
            )
            return {"success": True, "leverage": leverage_val, "symbol": symbol}
        except ValidationError:
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to set leverage",
                context={"symbol": symbol, "leverage": leverage, "exchange": self.exchange_type},
                error_message="Failed to set leverage"
            )

    @error_handler(
        context_extractor=lambda self: {"dummy": "set_position_mode"},
        log_message="set_position_mode failed"
    )
    async def set_position_mode(self) -> Dict:
        try:
            return await self._execute_request(
                method="POST",
                endpoint="/api/v5/account/set-position-mode",
                data={"posMode": "net_mode"}
            )
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to set position mode",
                context={"exchange": self.exchange_type},
                error_message="Failed to set position mode"
            )

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="cancel_all_orders failed"
    )
    async def cancel_all_orders(self, symbol: str) -> Dict:
        try:
            data = {"instId": symbol}
            regular_orders = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/cancel-batch-orders",
                data=data
            )

            response = await self._execute_request(
                method="GET",
                endpoint="/api/v5/trade/orders-algo-pending",
                params={"ordType": "trigger", "instId": symbol}
            )
            algos = response["data"][0] if response else None
            algo_orders = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/cancel-algos",
                data={"algoID": algos["algoId"], "instId": symbol}
            )
            return {"success": True, "regular_orders": regular_orders, "algo_orders": algo_orders}
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to cancel orders",
                context={"symbol": symbol, "exchange": self.exchange_type},
                error_message="Failed to cancel orders"
            )

    @error_handler(
        context_extractor=lambda self, symbol: {"symbol": symbol},
        log_message="close_position failed"
    )
    async def close_position(self, symbol: str) -> Dict:
        try:
            response = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/close-position",
                data={"instId": symbol, "mgnMode": "cross", "autoCxl": True}
            )
            return {"success": True, "result": response}
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to close position",
                context={"symbol": symbol, "exchange": self.exchange_type},
                error_message="Failed to close position"
            )

    @error_handler(
        context_extractor=lambda self, symbol, side, size, client_id, leverage: {
            "symbol": symbol, "side": side, "size": size, "client_id": client_id, "leverage": leverage
        },
        log_message="place_signal failed"
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
        try:
            position_status = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            if position_status.get("action_needed"):
                raise ExchangeError(
                    "Position handling failed",
                    context={"status": position_status, "symbol": symbol, "side": side}
                )
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]
            order_params = {
                "instId": symbol,
                "tdMode": "cross",
                "side": side,
                "ordType": "limit",
                "sz": size,
                "px": str(entry_price),
                "clOrdId": client_id
            }
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
                except (InvalidOperation, ValidationError) as e:
                    raise ValidationError(
                        "Invalid take profit value",
                        context={"take_profit": take_profit, "error": str(e)}
                    )
            order_result = await self._execute_request(
                method="POST",
                endpoint="/api/v5/trade/order",
                data=order_params
            )
            if order_result:
                order_id = order_result[0].get("ordId")
                if order_id:
                    monitor_result = await self.order_monitor(symbol, order_id)
                    order_result[0]["monitor_status"] = monitor_result
            return {"success": True, "order": order_result, "position_status": position_status}
        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to place signal order",
                context={"symbol": symbol, "side": side, "size": size, "exchange": self.exchange_type},
                error_message="Failed to place signal order"
            )

    @error_handler(
        context_extractor=lambda self, symbol, side, size, client_id, take_profit, leverage: {
            "symbol": symbol, "side": side, "size": size, "client_id": client_id, "take_profit": take_profit, "leverage": leverage
        },
        log_message="place_ladder failed"
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
        try:
            position_status = await self.handle_current_position(
                symbol=symbol,
                side=side,
                leverage=leverage
            )
            if position_status.get("action_needed"):
                raise ExchangeError(
                    "Position handling failed",
                    context={"status": position_status, "symbol": symbol, "side": side}
                )
            await self.cancel_all_orders(symbol)
            prices = await self.get_current_price(symbol)
            entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]
            try:
                validated_tp = await self.validate_price(
                    symbol=symbol,
                    side=side,
                    price_type="take_profit",
                    price=Decimal(take_profit)
                )
            except (InvalidOperation, ValidationError) as e:
                raise ValidationError(
                    "Invalid take profit value",
                    context={"take_profit": take_profit, "error": str(e)}
                )
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
            if order_result:
                order_id = order_result[0].get("ordId")
                if order_id:
                    monitor_result = await self.order_monitor(symbol, order_id)
                    order_result[0]["monitor_status"] = monitor_result
            return {"success": True, "order": order_result, "position_status": position_status}
        except (ValidationError, ExchangeError):
            raise
        except Exception as e:
            await self._handle_exception(
                e,
                log_message="Failed to place ladder order",
                context={"symbol": symbol, "side": side, "size": size, "exchange": self.exchange_type},
                error_message="Failed to place ladder order"
            )

    @error_handler(
        context_extractor=lambda self, symbol, order_type: {"symbol": symbol, "order_type": order_type},
        log_message="position_control failed"
    )
    async def position_control(self, symbol: str, order_type: str) -> Dict:
        cancel_result = await self.cancel_all_orders(symbol)
        if not cancel_result.get("success"):
            raise ExchangeError(
                "Failed to cancel orders",
                context={"symbol": symbol, "result": cancel_result}
            )
        close_result = await self.close_position(symbol)
        if not close_result.get("success"):
            exc = ExchangeError(
                "Failed to close position",
                context={"symbol": symbol, "close_result": close_result}
            )
            await handle_api_error(
                error=exc,
                context={"symbol": symbol, "close_result": close_result},
                log_message="Position closure failed during position control"
            )
            raise exc
        await self.set_position_mode()
        return {"success": True, "cancel_result": cancel_result, "close_result": close_result}


# Deferred imports to avoid circular dependencies
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.services.exchange.base import BaseExchange, ExchangeCredentials
from app.core.references import ExchangeType

logger = get_logger(__name__)
