"""
Revised main.py for Trading WebApp

Changes made:
- Centralized service initialization in the startup event (using app.state).
- Merged the HTTP middleware into a single unified function.
- Removed explicit start/stop calls for individual services by using lazy initialization.
- Assigned shared service instances (database, reference manager, performance service, Telegram bot, and WebSocket manager) to app.state.
- Included explicit calls to connect the database and start the WebSocket manager.
- Retained global exception handlers and API endpoints.
"""

import time
import uuid
from datetime import datetime
from typing import Dict, Any, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

# Core imports
from app.core.config import settings
from app.core.logging.logger import init_logging, get_logger, cleanup_logging
from app.core.errors.base import BaseError, ServiceError
from app.core.references import ErrorCategory

# API routes
from app.api.v1.api import api_router

# Service (singleton) imports
from app.db.db import db
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service
from app.services.telegram.service import telegram_bot
from app.services.websocket.manager import ws_manager

# Initialize logging
init_logging()
logger = get_logger(__name__)

# Create FastAPI application instance
app = FastAPI(
    title=settings.app.PROJECT_NAME,
    version=settings.app.VERSION,
    openapi_url=f"{settings.app.API_V1_STR}/openapi.json"
)

# ---------------------------
# Unified HTTP Middleware
# ---------------------------
@app.middleware("http")
async def unified_middleware(request: Request, call_next: Callable) -> Response:
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    context = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "timestamp": datetime.utcnow().isoformat()
    }
    # Maintenance mode: if enabled (and not a health/metrics endpoint), return 503.
    if settings.MAINTENANCE_MODE and not request.url.path.startswith(("/health", "/metrics")):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "System under maintenance", "retry_after": 300},
            headers={"Retry-After": "300"}
        )
    try:
        response = await call_next(request)
    except Exception as exc:
        process_time = time.time() - start_time
        logger.error("Unhandled exception in request", extra={"error": str(exc), "context": context, "process_time": process_time})
        raise
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}"
    response.headers["X-Request-ID"] = request_id
    logger.info("Request completed", extra={"method": request.method, "path": request.url.path, "process_time": process_time})
    return response

# ---------------------------
# CORS Middleware
# ---------------------------
if settings.cors.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.cors.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---------------------------
# Global Exception Handlers
# ---------------------------
@app.exception_handler(BaseError)
async def base_error_handler(request: Request, exc: BaseError):
    logger.error("A BaseError occurred", extra={"error": exc.to_dict(), "path": request.url.path})
    status_code = 400
    if exc.category in {
        ErrorCategory.DATABASE,
        ErrorCategory.EXCHANGE,
        ErrorCategory.NETWORK,
        ErrorCategory.WEBSOCKET,
        ErrorCategory.SYSTEM,
    }:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": exc.to_dict()}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", extra={"error": str(exc), "path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "detail": "Internal server error",
            "timestamp": datetime.utcnow().isoformat()
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    context = {
        "path": request.url.path,
        "method": request.method,
        "validation_errors": exc.errors(),
        "request_id": request.headers.get("X-Request-ID"),
        "timestamp": datetime.utcnow().isoformat()
    }
    errors = {" -> ".join(map(str, error["loc"])): error["msg"] for error in exc.errors()}
    logger.warning("Validation error", extra={"context": context, "errors": errors})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation error",
            "errors": errors,
            "request_id": context["request_id"],
            "timestamp": context["timestamp"]
        }
    )

# ---------------------------
# API Endpoints
# ---------------------------
@app.get("/health")
async def health_check() -> Dict[str, Any]:
    db_healthy = await db.health_check()
    ref_counts = await reference_manager.get_reference_counts()
    uptime = time.time() - app.state.start_time if hasattr(app.state, "start_time") else None
    return {
        "status": "ok" if db_healthy else "degraded",
        "version": settings.app.VERSION,
        "environment": settings.app.ENVIRONMENT,
        "database": {"connected": db_healthy, "references": ref_counts},
        "uptime": uptime,
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/")
async def root() -> Dict[str, str]:
    return {
        "name": settings.app.PROJECT_NAME,
        "version": settings.app.VERSION,
        "docs_url": "/docs",
        "openapi_url": f"{settings.app.API_V1_STR}/openapi.json"
    }

# ---------------------------
# Application Startup and Shutdown
# ---------------------------
@app.on_event("startup")
async def startup_event():
    """Application startup event.
    
    - Sets app.state.start_time.
    - Stores shared service instances (db, reference_manager, performance_service, telegram_bot, ws_manager).
    - Calls db.connect_db() to establish the database connection.
    - Starts the WebSocket manager.
    """
    app.state.start_time = time.time()
    # Store shared service instances on app.state for centralized access:
    app.state.db = db
    await db.connect_db()         # Connect to the database
    app.state.reference_manager = reference_manager
    app.state.performance_service = performance_service
    app.state.telegram_bot = telegram_bot
    from app.services.websocket.manager import ws_manager
    app.state.ws_manager = ws_manager
    await ws_manager.start()      # Start the WebSocket manager maintenance loop
    logger.info("Application startup complete", extra={"timestamp": datetime.utcnow().isoformat()})

@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event.
    
    - Calls telegram_bot.stop(), ws_manager.stop(), and db.close_db() for clean shutdown.
    - Calls cleanup_logging() to clean up log handlers.
    """
    try:
        await telegram_bot.stop()
    except Exception as e:
        logger.error("Error stopping Telegram bot", extra={"error": str(e)})
    try:
        await ws_manager.stop()
    except Exception as e:
        logger.error("Error stopping WebSocket manager", extra={"error": str(e)})
    try:
        await db.close_db()
    except Exception as e:
        logger.error("Error closing DB", extra={"error": str(e)})
    logger.info("Application shutdown complete", extra={"timestamp": datetime.utcnow().isoformat()})
    cleanup_logging()

# ---------------------------
# Include API Router
# ---------------------------
app.include_router(api_router, prefix=settings.app.API_V1_STR)
