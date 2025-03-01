"""
API router configuration with comprehensive safety features.

Features:
- Circuit breaker and rate limiting
- Maintenance mode checking
- Health and metrics endpoints
- Lazy registration of v1 endpoint routers
"""

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.logging.logger import get_logger
from app.core.config import settings
from app.core.errors.handlers import handle_api_error
from app.core.errors.base import ServiceError, RateLimitError

logger = get_logger(__name__)

# -------------------------------------------------------------------
# Request context model and helper function
# -------------------------------------------------------------------
class RequestContext(BaseModel):
    request_id: Optional[str]
    path: str
    method: str
    client_ip: str
    user_agent: Optional[str]
    timestamp: str

def create_request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=request.headers.get("X-Request-ID"),
        path=request.url.path,
        method=request.method,
        client_ip=request.client.host,
        user_agent=request.headers.get("User-Agent"),
        timestamp=datetime.utcnow().isoformat()
    )

# -------------------------------------------------------------------
# Circuit Breaker Implementation
# -------------------------------------------------------------------
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.is_open = False

    async def record_failure(self, context: Dict[str, Any]) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.warning("Circuit breaker opened", extra={
                "failures": self.failures,
                "threshold": self.failure_threshold,
                "context": context
            })

    async def can_execute(self) -> bool:
        if not self.is_open:
            return True
        if time.time() - (self.last_failure_time or 0) >= self.reset_timeout:
            self.is_open = False
            self.failures = 0
            logger.info("Circuit breaker reset")
            return True
        return False

    async def record_success(self) -> None:
        self.failures = 0
        self.is_open = False

    def get_state(self) -> Dict[str, Any]:
        return {
            "is_open": self.is_open,
            "failures": self.failures,
            "last_failure": self.last_failure_time,
            "threshold": self.failure_threshold
        }

# -------------------------------------------------------------------
# Rate Limiter Implementation
# -------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit: int, window: int) -> None:
        self.limit = limit
        self.window = window
        self.requests: Dict[str, List[float]] = {}

    async def check_rate_limit(self, client_id: str, context: Dict[str, Any]) -> bool:
        now = time.time()
        self.requests.setdefault(client_id, [])
        # Purge expired requests
        self.requests[client_id] = [t for t in self.requests[client_id] if now - t < self.window]
        if len(self.requests[client_id]) >= self.limit:
            logger.warning("Rate limit exceeded", extra={
                "client_id": client_id,
                "limit": self.limit,
                "window": self.window,
                "request_count": len(self.requests[client_id]),
                "context": context
            })
            return False
        self.requests[client_id].append(now)
        return True

# -------------------------------------------------------------------
# Helper functions to add common headers
# -------------------------------------------------------------------
def add_security_headers(response: Response) -> None:
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'",
        "Referrer-Policy": "strict-origin-when-cross-origin"
    })

def add_rate_limit_headers(response: Response, rate_limiter: RateLimiter, client_id: str) -> None:
    remaining = rate_limiter.limit - len(rate_limiter.requests.get(client_id, []))
    reset_time = int(time.time() + rate_limiter.window)
    response.headers.update({
        "X-Rate-Limit-Remaining": str(remaining),
        "X-Rate-Limit-Reset": str(reset_time)
    })

# -------------------------------------------------------------------
# Initialize API Router and dependencies
# -------------------------------------------------------------------
api_router = APIRouter()

critical_paths = ["/trading", "/bots", "/accounts"]
circuit_breakers: Dict[str, CircuitBreaker] = {
    path: CircuitBreaker(
        failure_threshold=settings.error.ERROR_RETRY_ATTEMPTS,
        reset_timeout=settings.error.ERROR_RETRY_DELAY
    ) for path in critical_paths
}

standard_rate_limiter = RateLimiter(
    limit=settings.rate_limiting.RATE_LIMIT_TRADES_PER_MINUTE,
    window=60
)
webhook_rate_limiter = RateLimiter(
    limit=settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND,
    window=1
)

def get_critical_breaker(path: str) -> Optional[CircuitBreaker]:
    for critical in critical_paths:
        if path.startswith(critical):
            return circuit_breakers[critical]
    return None

# -------------------------------------------------------------------
# Health Check and Metrics Endpoints
# -------------------------------------------------------------------
@api_router.get("/health")
async def health_check(request: Request) -> Dict[str, Any]:
    deps_status = {}
    deps_healthy = True

    try:
        from app.db import get_database
        db = await get_database()
        await db.ping()
        deps_status["database"] = "healthy"
    except Exception as e:
        deps_status["database"] = str(e)
        deps_healthy = False

    if settings.redis.REDIS_URL:
        try:
            from app.core.cache import get_redis
            redis = await get_redis()
            await redis.ping()
            deps_status["redis"] = "healthy"
        except Exception as e:
            deps_status["redis"] = str(e)
            deps_healthy = False

    deps_status["circuit_breakers"] = {path: breaker.get_state() for path, breaker in circuit_breakers.items()}
    return {
        "status": "healthy" if deps_healthy else "unhealthy",
        "environment": settings.app.ENVIRONMENT,
        "version": settings.app.VERSION,
        "dependencies": deps_status,
        "timestamp": datetime.utcnow().isoformat()
    }

@api_router.get("/metrics")
async def metrics(request: Request) -> Dict[str, Any]:
    return {
        "circuit_breakers": {path: breaker.get_state() for path, breaker in circuit_breakers.items()},
        "rate_limits": {
            "standard": {"limit": settings.rate_limiting.RATE_LIMIT_TRADES_PER_MINUTE, "window": 60},
            "webhook": {"limit": settings.rate_limiting.RATE_LIMIT_ORDERS_PER_SECOND, "window": 1}
        },
        "timestamp": datetime.utcnow().isoformat()
    }

# -------------------------------------------------------------------
# Lazy Import and Registration of Endpoint Routers
# -------------------------------------------------------------------
def get_routers():
    from app.api.v1.endpoints import (
        auth,
        trading,
        bots,
        accounts,
        groups,
        users,
        webhook,
        ws
    )
    return [
        (auth.router, "/auth", ["Authentication"]),
        (trading.router, "/trading", ["Trading Operations"]),
        (bots.router, "/bots", ["Bot Management"]),
        (accounts.router, "/accounts", ["Account Management"]),
        (groups.router, "/groups", ["Group Management"]),
        (users.router, "/users", ["User Management"]),
        (webhook.router, "/webhook", ["Webhooks"]),
        (ws.router, "/ws", ["WebSocket"])
    ]

for router_item, prefix, tags in get_routers():
    api_router.include_router(router_item, prefix=prefix, tags=tags)
