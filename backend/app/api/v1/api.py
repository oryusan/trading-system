"""
Enhanced API router configuration with comprehensive safety features.

Features:
- Enhanced circuit breaker pattern
- Request/response validation
- Security and monitoring
- Health checks and metrics
- Dependency management
"""

from fastapi import APIRouter, Request, Response, Depends, status
from fastapi.responses import JSONResponse
from typing import Callable, Dict, Any, Optional, List
import time
import asyncio
from datetime import datetime

# Import core types from references
from app.core.references import (
    ErrorContext,
    LogContext,
    Environment,
    ErrorLevel,
    RecoveryStrategy
)

# Lazy imports for dependency management
def get_logger():
    from app.core.logging.logger import get_logger
    return get_logger(__name__)

def get_settings():
    from app.core.config import settings
    return settings

def get_error_handler():
    from app.core.errors.handlers import handle_api_error
    return handle_api_error

logger = get_logger()
settings = get_settings()
handle_api_error = get_error_handler()

# Enhanced circuit breaker
class CircuitBreaker:
    """Enhanced circuit breaker pattern implementation."""
    
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = None
        self.is_open = False

    async def record_failure(self, context: ErrorContext) -> None:
        """Record a failure with context."""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                "Circuit breaker opened",
                extra={
                    "failures": self.failures,
                    "threshold": self.failure_threshold,
                    "context": context
                }
            )

    async def can_execute(self) -> bool:
        """Check if operation can execute."""
        if not self.is_open:
            return True

        if time.time() - self.last_failure_time >= self.reset_timeout:
            self.is_open = False
            self.failures = 0
            logger.info("Circuit breaker reset")
            return True

        return False

    async def record_success(self) -> None:
        """Record successful operation."""
        self.failures = 0
        self.is_open = False

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state."""
        return {
            "is_open": self.is_open,
            "failures": self.failures,
            "last_failure": self.last_failure_time,
            "threshold": self.failure_threshold
        }

# Enhanced rate limiter
class RateLimiter:
    """Enhanced rate limiting implementation."""
    
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.requests: Dict[str, List[float]] = {}

    async def check_rate_limit(self, client_id: str, context: Dict[str, Any]) -> bool:
        """Check if request is within rate limit."""
        now = time.time()
        
        if client_id not in self.requests:
            self.requests[client_id] = []
            
        # Remove expired requests
        self.requests[client_id] = [
            t for t in self.requests[client_id]
            if now - t < self.window
        ]
        
        request_count = len(self.requests[client_id])
        
        if request_count >= self.limit:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_id": client_id,
                    "limit": self.limit,
                    "window": self.window,
                    "request_count": request_count,
                    "context": context
                }
            )
            return False
            
        self.requests[client_id].append(now)
        return True

# Create API router
api_router = APIRouter()

# Initialize circuit breakers for critical endpoints
circuit_breakers: Dict[str, CircuitBreaker] = {}
critical_paths = ["/trading", "/bots", "/accounts"]

for path in critical_paths:
    circuit_breakers[path] = CircuitBreaker(
        failure_threshold=settings.ERROR_RETRY_ATTEMPTS,
        reset_timeout=settings.ERROR_RETRY_DELAY
    )

# Create rate limiters
standard_rate_limiter = RateLimiter(
    limit=settings.RATE_LIMIT_TRADES_PER_MINUTE,
    window=60
)

webhook_rate_limiter = RateLimiter(
    limit=settings.RATE_LIMIT_ORDERS_PER_SECOND,
    window=1
)

# Enhanced middleware
@api_router.middleware("http")
async def enhanced_middleware(request: Request, call_next: Callable) -> Response:
    """Enhanced middleware with monitoring and safety features."""
    start_time = time.time()
    
    # Create request context
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "path": request.url.path,
        "method": request.method,
        "client_ip": request.client.host,
        "user_agent": request.headers.get("User-Agent"),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        # Check maintenance mode
        if settings.MAINTENANCE_MODE and not context["path"].startswith(("/health", "/metrics")):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": "System under maintenance",
                    "retry_after": 300
                },
                headers={"Retry-After": "300"}
            )

        # Check circuit breaker
        if any(context["path"].startswith(path) for path in critical_paths):
            breaker = circuit_breakers[context["path"]]
            if not await breaker.can_execute():
                raise ServiceError(
                    "Service temporarily unavailable",
                    context={
                        **context,
                        "circuit_breaker": breaker.get_state()
                    }
                )

        # Check rate limits
        client_id = request.headers.get("X-API-Key") or request.client.host
        
        rate_limiter = (
            webhook_rate_limiter if context["path"].startswith("/webhook")
            else standard_rate_limiter
        )
        
        if not await rate_limiter.check_rate_limit(client_id, context):
            raise RateLimitError("Rate limit exceeded", context=context)

        # Process request
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Update circuit breaker on success
        if context["path"] in circuit_breakers:
            await circuit_breakers[context["path"]].record_success()

        # Add response headers
        response.headers.update({
            "X-Request-ID": str(context["request_id"]),
            "X-Process-Time": f"{process_time:.4f}",
            "X-Rate-Limit-Remaining": str(rate_limiter.limit - len(rate_limiter.requests.get(client_id, []))),
            "X-Rate-Limit-Reset": str(int(time.time() + rate_limiter.window))
        })

        # Add security headers
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        })

        # Log successful request
        logger.info(
            f"Request completed: {context['method']} {context['path']}",
            extra={
                **context,
                "status_code": response.status_code,
                "process_time": process_time
            }
        )

        return response

    except Exception as e:
        process_time = time.time() - start_time
        
        # Update error context
        error_context = {
            **context,
            "error": str(e),
            "process_time": process_time
        }
        
        # Update circuit breaker
        if context["path"] in circuit_breakers:
            await circuit_breakers[context["path"]].record_failure(error_context)
            
        await handle_api_error(
            error=e,
            context=error_context,
            log_message="Request failed"
        )

# Enhanced health check
@api_router.get("/health")
async def health_check(request: Request) -> Dict[str, Any]:
    """Enhanced health check endpoint."""
    try:
        # Check dependencies
        deps_healthy = True
        deps_status = {}
        
        # Database check
        try:
            from app.db import get_database
            db = await get_database()
            await db.ping()
            deps_status["database"] = "healthy"
        except Exception as e:
            deps_status["database"] = str(e)
            deps_healthy = False

        # Redis check if configured
        if settings.REDIS_URL:
            try:
                from app.core.cache import get_redis
                redis = await get_redis()
                await redis.ping()
                deps_status["redis"] = "healthy"
            except Exception as e:
                deps_status["redis"] = str(e)
                deps_healthy = False

        # Circuit breaker status
        deps_status["circuit_breakers"] = {
            path: breaker.get_state()
            for path, breaker in circuit_breakers.items()
        }
        
        return {
            "status": "healthy" if deps_healthy else "unhealthy",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION,
            "dependencies": deps_status,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        await handle_api_error(
            error=e,
            context={"check_time": datetime.utcnow().isoformat()},
            log_message="Health check failed"
        )

# Enhanced metrics endpoint
@api_router.get("/metrics")
async def metrics(request: Request) -> Dict[str, Any]:
    """Get service metrics."""
    try:
        metrics_data = {
            "circuit_breakers": {
                path: breaker.get_state()
                for path, breaker in circuit_breakers.items()
            },
            "rate_limits": {
                "standard": {
                    "limit": settings.RATE_LIMIT_TRADES_PER_MINUTE,
                    "window": 60
                },
                "webhook": {
                    "limit": settings.RATE_LIMIT_ORDERS_PER_SECOND,
                    "window": 1
                }
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return metrics_data
        
    except Exception as e:
        await handle_api_error(
            error=e,
            context={"path": request.url.path},
            log_message="Metrics collection failed"
        )

# Import routers lazily
def get_routers():
    """Get all API routers with proper service integration."""
    from app.api.v1.endpoints import (
        auth,
        trading,
        bots,
        accounts,
        groups,
        webhook,
        ws,
        #performance,   # New router for performance service
        #symbols,       # New router for symbol management
        #monitoring,    # New router for system monitoring
        #references     # New router for reference management
    )
    
    return [
        # Core operational routers
        (auth.router, "/auth", ["Authentication"], standard_rate_limiter),
        (trading.router, "/trading", ["Trading Operations"], standard_rate_limiter),
        (bots.router, "/bots", ["Bot Management"], standard_rate_limiter),
        (accounts.router, "/accounts", ["Account Management"], standard_rate_limiter),
        (groups.router, "/groups", ["Group Management"], standard_rate_limiter),
        
        # Service-specific routers
        #(performance.router, "/performance", ["Performance Tracking"], standard_rate_limiter),
        #(symbols.router, "/symbols", ["Symbol Management"], standard_rate_limiter),
        #(monitoring.router, "/monitoring", ["System Monitoring"], standard_rate_limiter),
        #(references.router, "/references", ["Reference Management"], standard_rate_limiter),
        
        # Integration routers
        (webhook.router, "/webhook", ["Webhooks"], webhook_rate_limiter),
        (ws.router, "/ws", ["WebSocket"], None)
    ]

# Register routers with dependencies
for router, prefix, tags, rate_limiter in get_routers():
    api_router.include_router(
        router,
        prefix=prefix,
        tags=tags,
        dependencies=[Depends(rate_limiter)] if rate_limiter else []
    )