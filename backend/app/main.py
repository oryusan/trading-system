"""
Enhanced FastAPI application with comprehensive error handling and service management.

Features:
- Sophisticated error handling and recovery
- Advanced service lifecycle management
- Comprehensive health monitoring
- Request validation and metrics
- Performance tracking
- WebSocket integration
"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from datetime import datetime
import time
import asyncio
from typing import Dict, Any, Optional

# Core imports
from app.core.config import settings
from app.core.logging.logger import init_logging, get_logger, cleanup_logging
from app.core.errors import (
    DatabaseError,
    ConfigurationError,
    ServiceError,
    ValidationError,
    ExchangeError,
    WebSocketError,
    AuthenticationError,
    AuthorizationError
)
from app.core.errors.handlers import handle_api_error
from app.core.references import WebSocketType

# API imports
from app.api.v1.api import api_router

# Service imports
from app.db.db import db
from app.services.exchange.factory import ExchangeFactory
from app.services.cron_jobs import cron_service
from app.services.bot_monitor import bot_monitor
from app.services.telegram.service import telegram_bot
from app.services.performance.service import performance_service 
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager

# Initialize logging
init_logging()
logger = get_logger(__name__)

async def verify_database() -> None:
    """
    Verify database connection and schema with comprehensive error handling.
    """
    context = {
        "action": "database_verification",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        await db.connect_db()
        await db.health_check()
        await db.recreate_indexes()
        
        # Get reference counts
        ref_counts = await reference_manager.get_reference_counts()
        logger.info(
            "Database verification complete",
            extra={
                **context,
                "reference_counts": ref_counts
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Database verification failed"
        )
        raise DatabaseError(
            "Database verification failed",
            context={"error": str(e)}
        )

async def init_exchange_services() -> None:
    """
    Initialize exchange services with enhanced error handling.
    """
    try:
        # Initialize factory settings
        await ExchangeFactory.init_settings()
        
        # Initialize WebSocket manager
        await ws_manager.start()
        
        # Warm up symbol cache
        for exchange_type in WebSocketType:
            try:
                await ExchangeFactory.refresh_symbol_cache(exchange_type)
            except Exception as e:
                logger.warning(
                    f"Symbol cache warmup failed for {exchange_type}",
                    extra={"error": str(e)}
                )

        logger.info("Exchange services initialized successfully")

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"service": "exchange"},
            log_message="Exchange service initialization failed"
        )
        raise ServiceError(
            "Exchange service initialization failed",
            context={"error": str(e)}
        )

async def start_services() -> None:
    """
    Start application services with comprehensive dependency management.
    """
    try:
        logger.info("Starting application services...")

        services = [
            ("Database", verify_database()),
            ("Reference Manager", reference_manager.start()),
            ("Exchange Services", init_exchange_services()),
            ("WebSocket Manager", ws_manager.start()),
            ("Performance Service", performance_service.start()),
            ("Cron Service", cron_service.start()),
            ("Bot Monitor", bot_monitor.start_monitoring()),
            ("Telegram Bot", telegram_bot.start())
        ]

        for service_name, coro in services:
            try:
                await coro
                logger.info(f"{service_name} started successfully")
            except Exception as e:
                logger.error(
                    f"{service_name} failed to start",
                    extra={
                        "service": service_name,
                        "error": str(e)
                    }
                )
                raise ServiceError(
                    f"{service_name} failed to start",
                    context={
                        "service": service_name,
                        "error": str(e)
                    }
                )

        logger.info("All application services started successfully")

    except Exception as e:
        await cleanup_services()
        await handle_api_error(
            error=e,
            context={"action": "service_startup"},
            log_message="Service startup failed"
        )
        raise ServiceError(
            "Service startup failed",
            context={"error": str(e)}
        )

async def cleanup_services() -> None:
    """
    Clean up services with enhanced error handling.
    """
    logger.info("Shutting down application services...")

    cleanup_tasks = [
        ("Telegram Bot", telegram_bot.stop()),
        ("Bot Monitor", bot_monitor.stop_monitoring()),
        ("Cron Service", cron_service.stop()),
        ("Performance Service", performance_service.stop()),
        ("WebSocket Manager", ws_manager.stop()),
        ("Exchange Factory", ExchangeFactory.close_all()),
        ("Reference Manager", reference_manager.stop()),
        ("Database", db.close_db())
    ]

    for service_name, coro in cleanup_tasks:
        try:
            await coro
            logger.info(f"{service_name} stopped successfully")
        except Exception as e:
            await handle_api_error(
                error=e,
                context={"service": service_name},
                log_message=f"Error stopping {service_name}"
            )

    cleanup_logging()
    logger.info("Application shutdown complete")

async def get_service_status() -> Dict[str, Any]:
    """
    Get comprehensive service health status.
    """
    try:
        ws_status = await ws_manager.get_status()
        ref_status = await reference_manager.get_status()
        perf_status = await performance_service.get_status()
        exchange_status = await ExchangeFactory.get_health_status()
        
        return {
            "websocket": ws_status,
            "reference": ref_status,
            "performance": perf_status,
            "exchange": exchange_status,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(
            "Failed to get service status",
            extra={"error": str(e)}
        )
        return {
            "error": "Failed to get service status",
            "timestamp": datetime.utcnow().isoformat()
        }

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS configuration
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """
    Enhanced request middleware with comprehensive error handling.
    """
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", str(time.time()))
    
    context = {
        "request_id": request_id,
        "path": request.url.path,
        "method": request.method,
        "client_ip": request.client.host if request.client else None,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        # Check service health
        if not reference_manager.is_healthy():
            raise ServiceError(
                "System services unavailable",
                context={
                    "status": await get_service_status(),
                    **context
                }
            )
        
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Add response headers
        response.headers.update({
            "X-Process-Time": str(process_time),
            "X-Request-ID": request_id
        })
        
        # Track request metrics
        await performance_service.track_request(
            path=request.url.path,
            method=request.method,
            status_code=response.status_code,
            duration=process_time
        )
        
        logger.info(
            "Request processed successfully",
            extra={
                **context,
                "process_time": process_time,
                "status_code": response.status_code
            }
        )
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        
        await handle_api_error(
            error=e,
            context={
                **context,
                "process_time": process_time
            },
            log_message="Request processing failed"
        )
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle request validation errors with enhanced error context.
    """
    context = {
        "path": request.url.path,
        "method": request.method,
        "validation_errors": exc.errors(),
        "request_id": request.headers.get("X-Request-ID"),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        # Format validation errors
        errors = {}
        for error in exc.errors():
            location = " -> ".join(str(loc) for loc in error["loc"])
            errors[location] = error["msg"]
            
        logger.warning(
            "Request validation failed",
            extra={
                **context,
                "errors": errors
            }
        )
        
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Validation error",
                "errors": errors,
                "request_id": context["request_id"],
                "timestamp": context["timestamp"]
            }
        )
        
    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Validation error handling failed"
        )
        raise

@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Enhanced health check endpoint with comprehensive status.
    """
    try:
        # Get database status
        db_healthy = await db.health_check()
        ref_counts = await reference_manager.get_reference_counts()
        
        # Get service status
        service_status = await get_service_status()
        
        # Get performance metrics
        metrics = await performance_service.get_system_metrics()
        
        return {
            "status": "ok" if db_healthy and all(
                s.get("healthy", False) 
                for s in service_status.values()
            ) else "degraded",
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
            "database": {
                "connected": db_healthy,
                "references": ref_counts
            },
            "services": service_status,
            "metrics": metrics,
            "uptime": time.time() - app.state.start_time,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"endpoint": "health"},
            log_message="Health check failed"
        )
        raise ServiceError(
            "Health check failed",
            context={"error": str(e)}
        )

@app.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint with application information."""
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "openapi_url": f"{settings.API_V1_STR}/openapi.json"
    }

@app.on_event("startup")
async def startup_event():
    """
    Application startup with enhanced service initialization.
    """
    try:
        app.state.start_time = time.time()
        await start_services()
        
        logger.info(
            "Application startup complete",
            extra={"timestamp": datetime.utcnow().isoformat()}
        )
        
    except Exception as e:
        logger.critical(
            "Application startup failed",
            extra={"error": str(e)}
        )
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """
    Application shutdown with comprehensive cleanup.
    """
    try:
        await cleanup_services()
        logger.info(
            "Application shutdown complete",
            extra={"timestamp": datetime.utcnow().isoformat()}
        )
        
    except Exception as e:
        logger.error(
            "Error during shutdown",
            extra={"error": str(e)}
        )

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)