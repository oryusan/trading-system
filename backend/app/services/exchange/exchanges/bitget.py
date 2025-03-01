"""
Bitget exchange implementation with enhanced service integration.

Features:
- Trading service integration
- WebSocket manager integration
- Centralized error handling via decorators
- Improved logging and reference validation
"""

from typing import Dict, List, Optional
from decimal import Decimal, InvalidOperation
import hmac
import base64
import hashlib
import json
from datetime import datetime

from app.core.errors.decorators import error_handler
from app.core.errors.base import (
    ValidationError,
    ExchangeError,
    RequestException
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.services.exchange.base import BaseExchange, ExchangeCredentials
from app.core.references import ExchangeType

logger = get_logger(__name__)


class BitgetExchange(BaseExchange):
    """
    Bitget-specific exchange implementation with service integration.

    This class implements the core functionality for interacting with Bitget's API.
    Error handling is centralized using the error_handler decorator.
    """

    @error_handler
    def __init__(self, credentials: ExchangeCredentials):
        """Initialize BitgetExchange."""
        super().__init__(credentials)
        self.exchange_type = ExchangeType.Bitget
        self.product_type = "SUSDT-FUTURES" if credentials.testnet else "USDT-FUTURES"
        self.margin_coin = "SUSDT" if credentials.testnet else "USDT"
        self._rate_limit = 20
        self.logger = get_logger("bitget_exchange")
        self.base_url = self._get_base_url()

    def _get_base_url(self) -> str:
        """Get Bitget API base URL."""
        return "https://api.bitget.com"

    @error_handler
    async def _handle_exception(self, e: Exception, context: dict, log_message: str, error_msg: str) -> None:
        """
        Log an error and then raise an ExchangeError with the given context.
        """
        await handle_api_error(error=e, context=context, log_message=log_message)
        raise ExchangeError(error_msg, context={**context, "error": str(e)})

    @error_handler
    async def _sign_request(
        self,
        timestamp: str,
        method: str,
        endpoint: str,
        body: str = ""
    ) -> Dict[str, str]:
        """Sign Bitget API request."""
        message = timestamp + method.upper() + endpoint + body
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

    @error_handler
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
        body = json.dumps(data) if method.upper() == "POST" and data else ""
        headers = await self._sign_request(timestamp, method, endpoint, body)

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
                if result.get("code") != "00000":
                    exc = RequestException(
                        result.get("msg", "Unknown error"),
                        context={
                            "response": result,
                            "endpoint": endpoint,
                            "exchange": self.exchange_type
                        }
                    )
                    await handle_api_error(
                        error=exc,
                        context={"response": result, "endpoint": endpoint},
                        log_message=f"API request failed: {endpoint}"
                    )
                    raise exc
                return result.get("data", {})

    @error_handler
    async def _fetch_symbol_info_from_exchange(
        self,
        symbol: str
    ) -> Dict[str, Decimal]:
        """Fetch symbol specifications from Bitget."""
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
                context={"exchange": self.exchange_type, "symbol": symbol}
            )
        instrument = response["list"][0]
        try:
            tick_size = Decimal(instrument["priceEndStep"]) / (10 ** Decimal(instrument["pricePlace"]))
            lot_size = Decimal(instrument["sizeMultiplier"])
            contract_size = Decimal("1")
        except Exception as e:
            await self._handle_exception(
                e,
                {"exchange": self.exchange_type, "symbol": symbol, "instrument_data": instrument},
                "Invalid symbol specifications",
                "Invalid symbol specifications"
            )
        return {
            "tick_size": tick_size,
            "lot_size": lot_size,
            "contract_size": contract_size
        }

    @error_handler
    async def get_current_price(self, symbol: str) -> Dict[str, Decimal]:
        """Get current price information for a symbol."""
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
                context={"symbol": symbol, "exchange": self.exchange_type}
            )
        data = response["list"][0]
        try:
            return {
                "last_price": Decimal(data["lastPr"]),
                "bid_price": Decimal(data["bidPr"]),
                "ask_price": Decimal(data["askPr"])
            }
        except (KeyError, InvalidOperation) as e:
            await self._handle_exception(
                e,
                {"symbol": symbol, "price_data": data},
                "Invalid price data format",
                "Invalid price data"
            )

    @error_handler
    async def get_balance(self, currency: str = "USDT") -> Dict[str, Decimal]:
        """Get account balance information."""
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
                context={"currency": currency, "exchange": self.exchange_type}
            )
        data = response["list"][0]
        try:
            return {
                "balance": Decimal(data["available"]),
                "equity": Decimal(data["accountEquity"])
            }
        except (KeyError, InvalidOperation) as e:
            await self._handle_exception(
                e,
                {"currency": currency, "balance_data": data},
                "Invalid balance data format",
                "Invalid balance data"
            )

    @error_handler
    async def get_all_positions(self) -> List[Dict]:
        """Get all open positions."""
        response = await self._execute_request(
            method="GET",
            endpoint="/api/v2/mix/position/all-position",
            params={
                "productType": self.product_type,
                "marginCoin": self.margin_coin
            }
        )
        positions = response if isinstance(response, list) else []
        return [pos for pos in positions if Decimal(pos.get("size", "0")) != Decimal("0")]

    @error_handler
    async def get_position(self, symbol: str) -> Optional[Dict]:
        """Get current position for a specific symbol."""
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

    @error_handler
    async def get_position_history(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """Get closed position history."""
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
                    "pnl_ratio": (Decimal(pos["pnl"]) /
                                  (Decimal(pos["openTotalPos"]) * Decimal(pos["openAvgPrice"]))) * Decimal("100"),
                    "opened_at": datetime.fromtimestamp(int(pos["cTime"]) / 1000),
                    "closed_at": datetime.fromtimestamp(int(pos["uTime"]) / 1000)
                })
            except (KeyError, InvalidOperation) as e:
                self.logger.warning(
                    f"Failed to process position: {pos}",
                    extra={"error": str(e)}
                )
        return positions

    @error_handler
    async def _get_position_side(self, position: Dict) -> str:
        """Get position side (buy/sell)."""
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

    @error_handler
    async def _is_position_empty(self, position: Dict) -> bool:
        """Check if position is empty."""
        if not isinstance(position, dict):
            raise ValidationError(
                "Invalid position data type",
                context={"position_type": type(position).__name__}
            )
        try:
            return Decimal(position.get("size", "0")) == Decimal("0")
        except InvalidOperation as e:
            raise ValidationError(
                "Invalid position size value",
                context={"position": position, "error": str(e)}
            )

    @error_handler
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """Get the status of a specific order."""
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

    @error_handler
    async def amend_order(self, symbol: str, order_id: str, new_price: Decimal) -> Dict:
        """Amend an existing order's price."""
        if new_price <= 0:
            raise ValidationError(
                "Price must be positive",
                context={"price": str(new_price), "symbol": symbol}
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

    @error_handler
    async def set_leverage(self, symbol: str, leverage: str) -> Dict:
        """Set leverage for both buy and sell sides."""
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

    @error_handler
    async def set_position_mode(self) -> Dict:
        """Set the position mode."""
        return await self._execute_request(
            method="POST",
            endpoint="/api/v2/mix/account/set-position-mode",
            data={
                "productType": self.product_type,
                "posMode": "one_way_mode"
            }
        )

    @error_handler
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        """Cancel all pending orders."""
        data = {
            "productType": self.product_type,
            "marginCoin": self.margin_coin
        }
        if symbol:
            data["symbol"] = symbol
        await self._execute_request(
            method="POST",
            endpoint="/api/v2/mix/order/cancel-all-orders",
            data=data
        )
        await self._execute_request(
            method="POST",
            endpoint="/api/v2/mix/order/cancel-plan-order",
            data={**data, "planType": "normal_plan"}
        )
        return {"success": True}

    @error_handler
    async def close_position(self, symbol: str) -> Dict:
        """Close an open position for a symbol."""
        response = await self._execute_request(
            method="POST",
            endpoint="/api/v2/mix/order/close-positions",
            data={
                "symbol": symbol,
                "productType": self.product_type
            }
        )
        return {"success": True, "result": response}

    @error_handler
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
        position_status = await self.handle_current_position(symbol=symbol, side=side, leverage=leverage)
        if position_status.get("action_needed"):
            raise ExchangeError(
                "Position handling failed",
                context={"symbol": symbol, "status": position_status}
            )
        prices = await self.get_current_price(symbol)
        entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]
        order_params = {
            "symbol": symbol,
            "productType": self.productType,
            "marginMode": "crossed",
            "marginCoin": self.margin_coin,
            "size": size,
            "price": str(entry_price),
            "side": side,
            "orderType": "limit",
            "clientOid": client_id
        }
        if take_profit:
            validated_tp = await self.validate_price(
                symbol=symbol,
                side=side,
                price_type="take_profit",
                price=Decimal(take_profit)
            )
            order_params["presetStopSurplusPrice"] = str(validated_tp)
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
        return {"success": True, "order": order_result, "position_status": position_status}

    @error_handler
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
        position_status = await self.handle_current_position(symbol=symbol, side=side, leverage=leverage)
        if position_status.get("action_needed"):
            raise ExchangeError(
                "Position handling failed",
                context={"symbol": symbol, "status": position_status}
            )
        await self.cancel_all_orders(symbol)
        prices = await self.get_current_price(symbol)
        entry_price = prices["bid_price"] if side == "buy" else prices["ask_price"]
        validated_tp = await self.validate_price(
            symbol=symbol,
            side=side,
            price_type="take_profit",
            price=Decimal(take_profit)
        )
        order_params = {
            "symbol": symbol,
            "productType": self.productType,
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
            if order_id:
                monitor_result = await self.order_monitor(symbol, order_id)
                order_result["monitor_status"] = monitor_result
        return {"success": True, "order": order_result, "position_status": position_status}

    @error_handler
    async def position_control(self, symbol: str, order_type: str) -> Dict:
        """Control existing position."""
        cancel_result = await self.cancel_all_orders(symbol)
        if not cancel_result.get("success"):
            raise ExchangeError(
                "Failed to cancel orders",
                context={"symbol": symbol, "cancel_result": cancel_result}
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
