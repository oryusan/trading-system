"""
Webhook endpoints for handling TradingView signals with comprehensive error handling.

Features:
- Webhook signature verification
- Signal validation and processing  
- Multi-account trade execution
- Signal forwarding
- Performance monitoring
"""

from fastapi import APIRouter, Request, Header, Depends
from pydantic import BaseModel, Field, validator
import hmac
import hashlib
import aiohttp
from datetime import datetime
import uuid
from enum import Enum
from typing import Dict, Optional, Any

# Core imports
from app.core.config import settings
from app.core.errors import (
    ValidationError,
    ExchangeError, 
    NotFoundError,
    AuthenticationError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    OrderType,
    TradeSource,
    ServiceResponse,
    WebhookVerification,
    PositionSide,
    SignalOrderType
)

router = APIRouter()
logger = get_logger(__name__)

class TradeSignal(BaseModel):
    """Trading signal model with enhanced validation."""
    order_type: SignalOrderType = Field(..., description="Signal order type")
    symbol: str = Field(..., description="Trading symbol")
    botname: str = Field(..., description="Target bot name")
    side: Optional[PositionSide] = Field(None, description="Trade side")
    size: Optional[str] = Field(None, description="Position size")
    leverage: Optional[str] = Field(None, description="Position leverage") 
    takeprofit: Optional[str] = Field(None, description="Take profit level")

    @validator("side", "size", "leverage")
    def validate_signal_fields(cls, v, values):
        """Validate required fields based on order type."""
        order_type = values.get("order_type")
        
        if order_type in [SignalOrderType.LONG_SIGNAL, SignalOrderType.SHORT_SIGNAL]:
            if not all([values.get("side"), values.get("size"), values.get("leverage")]):
                raise ValidationError(
                    "Signal orders require side, size, and leverage",
                    context={
                        "order_type": order_type,
                        "missing_fields": [
                            f for f in ["side", "size", "leverage"] 
                            if not values.get(f)
                        ]
                    }
                )

        if order_type in [SignalOrderType.LONG_LADDER, SignalOrderType.SHORT_LADDER]:
            if not all([values.get("side"), values.get("size"), values.get("leverage"), values.get("takeprofit")]):
                raise ValidationError(
                    "Ladder orders require side, size, leverage, and takeprofit",
                    context={
                        "order_type": order_type,
                        "missing_fields": [
                            f for f in ["side", "size", "leverage", "takeprofit"]
                            if not values.get(f)
                        ]
                    }
                )

        return v

    @validator("symbol")
    def validate_symbol(cls, v):
        """Validate symbol format."""
        if not v or not v.strip():
            raise ValidationError(
                "Symbol cannot be empty",
                context={"symbol": v}
            )
        return v.strip().upper()

    @validator("botname")
    def validate_botname(cls, v):
        """Validate bot name format."""
        if not v or not v.strip():
            raise ValidationError(
                "Bot name cannot be empty",
                context={"botname": v}
            )
        return v.strip()

    @validator("size")
    def validate_size(cls, v):
        """Validate size format."""
        if v is not None:
            try:
                size = float(v)
                if size <= 0 or size > 100:
                    raise ValidationError(
                        "Size must be between 0 and 100",
                        context={"size": v}
                    )
            except ValueError:
                raise ValidationError(
                    "Invalid size format",
                    context={"size": v}
                )
        return v

    @validator("leverage")
    def validate_leverage(cls, v):
        """Validate leverage format."""
        if v is not None:
            try:
                lev = int(v)
                if lev <= 0 or lev > 100:
                    raise ValidationError(
                        "Leverage must be between 1 and 100",
                        context={"leverage": v}
                    )
            except ValueError:
                raise ValidationError(
                    "Invalid leverage format",
                    context={"leverage": v}
                )
        return v

    @validator("takeprofit")
    def validate_takeprofit(cls, v):
        """Validate takeprofit format."""
        if v is not None:
            try:
                tp = float(v)
                if tp <= 0:
                    raise ValidationError(
                        "Take profit must be positive",
                        context={"takeprofit": v}
                    )
            except ValueError:
                raise ValidationError(
                    "Invalid take profit format",
                    context={"takeprofit": v}
                )
        return v

    @validator("side")
    def validate_side_consistency(cls, v, values):
        """Validate side is consistent with order type."""
        order_type = values.get("order_type")
        if order_type and v:
            if order_type.startswith("Long") and v != PositionSide.LONG:
                raise ValidationError(
                    "Side must be long for long orders",
                    context={
                        "order_type": order_type,
                        "side": v
                    }
                )
            if order_type.startswith("Short") and v != PositionSide.SHORT:
                raise ValidationError(
                    "Side must be short for short orders",
                    context={
                        "order_type": order_type,
                        "side": v
                    }
                )
        return v

@router.post("/tradingview", response_model=ServiceResponse)
async def tradingview_webhook(
    request: Request,
    x_tradingview_signature: str = Header(None)
) -> ServiceResponse:
    """Handle TradingView webhook signals with comprehensive error handling."""
    correlation_id = str(uuid.uuid4())
    context = {
        "correlation_id": correlation_id,
        "request_id": request.headers.get("X-Request-ID"),
        "timestamp": datetime.utcnow().isoformat(),
        "path": request.url.path
    }
    
    try:
        # Log incoming webhook request
        logger.info(f"Received webhook request with headers: {dict(request.headers)}")

        # Verify signature
        body = await request.body()
        if not await verify_webhook_signature(x_tradingview_signature, body, context):
            raise AuthenticationError(
                "Invalid webhook signature",
                context={
                    **context,
                    "signature": x_tradingview_signature
                }
            )

        # Parse and validate signal
        try:
            data = await request.json()
            signal = TradeSignal(**data)
        except ValidationError as e:
            raise ValidationError(
                "Invalid signal data",
                context={
                    **context,
                    "errors": str(e),
                    "data": data if 'data' in locals() else None
                }
            )

        # Validate bot reference
        bot = await reference_manager.get_reference(
            reference_type="Bot",
            reference_id=signal.botname,
            filter_params={"name": signal.botname}
        )
        if not bot:
            raise NotFoundError(
                "Bot not found",
                context={
                    **context,
                    "botname": signal.botname
                }
            )

        # Check bot status
        if bot.get("status") != BotStatus.ACTIVE:
            logger.info(
                "Signal ignored - inactive bot",
                extra={
                    **context,
                    "bot_id": str(bot.get("id")),
                    "status": bot.get("status")
                }
            )
            return ServiceResponse(
                success=False,
                message=f"Bot {signal.botname} is not active",
                data={
                    "bot": signal.botname,
                    "status": bot.get("status"),
                    "correlation_id": correlation_id
                }
            )

        # Forward webhook if configured  
        await forward_webhook(signal.dict(), context)

        # Get trading service
        trading_service = await reference_manager.get_service(
            service_type="TradingService"
        )

        # Process signal
        results = await trading_service.process_signal(
            bot_id=str(bot.get("id")),
            signal_data={
                "symbol": signal.symbol,
                "side": signal.side,
                "order_type": signal.order_type,
                "size": signal.size,
                "leverage": signal.leverage,
                "take_profit": signal.takeprofit,
                "source": TradeSource.TRADINGVIEW
            },
            context=context
        )

        # Update performance metrics
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
            data={
                **results,
                "correlation_id": correlation_id
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Webhook processing failed"
        )

@router.get("/tradingview/test")
async def test_webhook() -> ServiceResponse:
    """Test webhook endpoint health."""
    context = {
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        logger.info(
            "Webhook test accessed",
            extra=context
        )
        
        return ServiceResponse(
            success=True,
            message="Webhook endpoint is operational",
            data={
                "timestamp": context["timestamp"],
                "correlation_id": context["correlation_id"]
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Webhook test failed"
        )

async def verify_webhook_signature(
    signature: str,
    body: bytes,
    context: Dict
) -> bool:
    """Verify TradingView webhook signature."""
    if not settings.TRADINGVIEW_WEBHOOK_SECRET.get_secret_value():
        raise AuthenticationError("Webhook secret is not set. Check environment variables.")

    try:
        expected = hmac.new(
            settings.TRADINGVIEW_WEBHOOK_SECRET.get_secret_value().encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(signature, expected)
        
        if not is_valid:
            raise AuthenticationError(
                "Invalid webhook signature",
                context={
                    **context,
                    "provided": signature
                }
            )
            
        return True

    except Exception as e:
        raise AuthenticationError(
            "Signature verification failed",
            context={
                **context,
                "error": str(e)
            }
        )

async def forward_webhook(data: Dict, context: Dict) -> None:
    """Forward webhook data if configured."""
    if not settings.WEBHOOK_FORWARD_URL:
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                settings.WEBHOOK_FORWARD_URL,
                json=data,
                timeout=settings.WEBHOOK_TIMEOUT
            ) as response:
                response.raise_for_status()

        logger.info(
            "Webhook forwarded successfully",
            extra={
                **context,
                "forward_url": settings.WEBHOOK_FORWARD_URL
            }
        )

    except Exception as e:
        logger.error(
            "Failed to forward webhook",
            extra={
                **context,
                "error": str(e),
                "forward_url": settings.WEBHOOK_FORWARD_URL
            }
        )

# Import models at end
from app.models.bot import Bot, BotStatus

# Import services at end to avoid circular dependencies
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.telegram.service import telegram_bot