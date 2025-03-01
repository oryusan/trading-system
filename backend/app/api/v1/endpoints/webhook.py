"""
Webhook endpoints for handling TradingView signals with comprehensive error handling.

Features:
- Webhook signature verification
- Signal validation and processing  
- Multi-account trade execution
- Signal forwarding
- Performance monitoring
"""

import uuid
import hmac
import hashlib
from datetime import datetime
from typing import Dict, Optional, Any

import aiohttp
from fastapi import APIRouter, Request, Header
from pydantic import BaseModel, Field, validator, model_validator

from app.core.config import settings
from app.core.errors.base import ValidationError, ExchangeError, NotFoundError, AuthenticationError
from app.core.logging.logger import get_logger
from app.core.references import OrderType, TradeSource, PositionSide, SignalOrderType
from app.api.v1.endpoints.trading import get_request_context
from app.api.v1.references import ServiceResponse

router = APIRouter()
logger = get_logger(__name__)


class TradeSignal(BaseModel):
    """Trading signal model with enhanced validation."""
    order_type: SignalOrderType = Field(..., description="Signal order type")
    symbol: str = Field(..., description="Trading symbol")
    botname: str = Field(..., description="Target bot name")
    side: Optional[PositionSide] = Field(None, description="Trade side")
    risk_percentage: Optional[str] = Field(None, description="Risk percentage")
    leverage: Optional[str] = Field(None, description="Position leverage")
    takeprofit: Optional[str] = Field(None, description="Take profit level")

    @validator("symbol")
    def validate_symbol(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValidationError("Symbol cannot be empty", context={"symbol": v})
        return v.strip().upper()

    @validator("botname")
    def validate_botname(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValidationError("Bot name cannot be empty", context={"botname": v})
        return v.strip()

    @validator("risk_percentage")
    def validate_risk_percentage(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                risk_val = float(v)
                if not (0 < risk_val <= 100):
                    raise ValidationError("Risk must be between 0 and 100", context={"risk_percentage": v})
            except ValueError:
                raise ValidationError("Invalid risk format", context={"risk_percentage": v})
        return v

    @validator("leverage")
    def validate_leverage(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                lev = int(v)
                if not (1 <= lev <= 100):
                    raise ValidationError("Leverage must be between 1 and 100", context={"leverage": v})
            except ValueError:
                raise ValidationError("Invalid leverage format", context={"leverage": v})
        return v

    @validator("takeprofit")
    def validate_takeprofit(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                tp = float(v)
                if tp <= 0:
                    raise ValidationError("Take profit must be positive", context={"takeprofit": v})
            except ValueError:
                raise ValidationError("Invalid take profit format", context={"takeprofit": v})
        return v

    @model_validator(mode="before")
    def check_required_fields_and_consistency(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        order_type = values.get("order_type")
        side = values.get("side")
        if order_type in {SignalOrderType.LONG_SIGNAL, SignalOrderType.SHORT_SIGNAL}:
            required_fields = ["side", "risk_percentage", "leverage"]
        elif order_type in {SignalOrderType.LONG_LADDER, SignalOrderType.SHORT_LADDER}:
            required_fields = ["side", "risk_percentage", "leverage", "takeprofit"]
        else:
            required_fields = []
        missing = [field for field in required_fields if not values.get(field)]
        if missing:
            raise ValidationError(
                f"Missing required fields for order type {order_type}: {', '.join(missing)}",
                context={"order_type": order_type, "missing_fields": missing}
            )
        if side and order_type:
            if order_type in {SignalOrderType.LONG_SIGNAL, SignalOrderType.LONG_LADDER} and side != PositionSide.LONG:
                raise ValidationError("Side must be LONG for long orders", context={"order_type": order_type, "side": side})
            if order_type in {SignalOrderType.SHORT_SIGNAL, SignalOrderType.SHORT_LADDER} and side != PositionSide.SHORT:
                raise ValidationError("Side must be SHORT for short orders", context={"order_type": order_type, "side": side})
        return values


@router.post("/tradingview", response_model=ServiceResponse)
async def tradingview_webhook(
    request: Request,
    x_tradingview_signature: str = Header(None)
) -> ServiceResponse:
    """
    Handle TradingView webhook signals.
    """
    correlation_id = str(uuid.uuid4())
    context = get_request_context(
        request,
        correlation_id=correlation_id
    )
    logger.info("Received webhook request", extra=context)

    # Verify signature.
    body = await request.body()
    if not await verify_webhook_signature(x_tradingview_signature, body, context):
        raise AuthenticationError("Invalid webhook signature", context={**context, "signature": x_tradingview_signature})
    
    # Parse JSON payload.
    try:
        data = await request.json()
    except Exception as e:
        raise ValidationError("Invalid JSON payload", context={**context, "error": str(e)})
    
    # Parse and validate signal.
    try:
        signal = TradeSignal(**data)
    except Exception as e:
        raise ValidationError("Invalid signal data", context={**context, "errors": str(e), "data": data})
    
    # Validate bot reference.
    bot = await reference_manager.get_reference(
        reference_type="Bot",
        reference_id=signal.botname,
        filter_params={"name": signal.botname}
    )
    if not bot:
        raise NotFoundError("Bot not found", context={**context, "botname": signal.botname})
    
    # Check bot status.
    # Import here to avoid circular dependency.
    from app.models.entities.bot import BotStatus
    if bot.get("status") != BotStatus.ACTIVE:
        logger.info("Signal ignored - inactive bot", extra={**context, "bot_id": str(bot.get("id")), "status": bot.get("status")})
        return ServiceResponse(
            success=False,
            message=f"Bot {signal.botname} is not active",
            data={
                "bot": signal.botname,
                "status": bot.get("status"),
                "correlation_id": correlation_id
            }
        )
    
    # Forward webhook if configured.
    await forward_webhook(signal.dict(), context)
    
    # Process signal via trading service
    trading_service_instance = await reference_manager.get_service(service_type="TradingService")
    results = await trading_service_instance.process_signal(
        bot_id=str(bot.get("id")),
        signal_data={
            "symbol": signal.symbol,
            "side": signal.side,
            "signal_type": signal.order_type,
            "risk_percentage": signal.risk_percentage,
            "leverage": signal.leverage,
            "take_profit": signal.takeprofit,
            "source": TradeSource.BOT
        },
        context=context
    )
    
    # Update performance metrics.
    await performance_service.update_signal_metrics(
        bot_id=str(bot.get("id")),
        metrics={
            "total_signals": results["total_signals"],
            "successful_signals": results["successful_signals"],
            "failed_signals": results["failed_signals"],
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    return ServiceResponse(
        success=True,
        message="Signal processed successfully",
        data={**results, "correlation_id": correlation_id}
    )


@router.get("/tradingview/test")
async def test_webhook() -> ServiceResponse:
    """
    Test webhook endpoint health.
    """
    context = {
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat()
    }
    logger.info("Webhook test accessed", extra=context)
    return ServiceResponse(
        success=True,
        message="Webhook endpoint is operational",
        data={
            "timestamp": context["timestamp"],
            "correlation_id": context["correlation_id"]
        }
    )


async def verify_webhook_signature(signature: str, body: bytes, context: Dict) -> bool:
    """
    Verify TradingView webhook signature.
    """
    secret = settings.webhook.TRADINGVIEW_WEBHOOK_SECRET.get_secret_value()
    if not secret:
        raise AuthenticationError("Webhook secret is not set. Check environment variables.")
    try:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError("Invalid webhook signature", context={**context, "provided": signature})
        return True
    except Exception as e:
        raise AuthenticationError("Signature verification failed", context={**context, "error": str(e)})


async def forward_webhook(data: Dict, context: Dict) -> None:
    """
    Forward webhook data if a forward URL is configured.
    """
    if not settings.webhook.WEBHOOK_FORWARD_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                settings.webhook.WEBHOOK_FORWARD_URL,
                json=data,
                timeout=settings.webhook.WEBHOOK_TIMEOUT
            ) as response:
                response.raise_for_status()
        logger.info("Webhook forwarded successfully", extra={**context, "forward_url": settings.webhook.WEBHOOK_FORWARD_URL})
    except Exception as e:
        logger.error("Failed to forward webhook", extra={**context, "error": str(e), "forward_url": settings.webhook.WEBHOOK_FORWARD_URL})


# ---- Circular Dependency Imports ----
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service