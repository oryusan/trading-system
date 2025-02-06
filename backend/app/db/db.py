"""
Enhanced MongoDB connection management with comprehensive error handling.

Features:
- Connection pooling and retry logic 
- Health checks and monitoring
- Beanie ODM initialization
- Structured error handling
- Performance tracking
- Reference validation
"""

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from typing import Optional, Dict, Any, List
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

class DatabaseMetrics:
    """Track database metrics and performance."""
    
    def __init__(self):
        self.total_connections = 0
        self.failed_connections = 0
        self.reconnect_attempts = 0
        self.operations = {
            "reads": 0,
            "writes": 0,
            "queries": 0
        }
        self.response_times: List[float] = []
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[datetime] = None
        
    def add_response_time(self, time_ms: float) -> None:
        """Add response time measurement."""
        self.response_times = self.response_times[-99:] + [time_ms]  # Keep last 100
        
    def get_average_response(self) -> float:
        """Get average response time."""
        return sum(self.response_times) / len(self.response_times) if self.response_times else 0
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "connections": {
                "total": self.total_connections,
                "failed": self.failed_connections,
                "reconnects": self.reconnect_attempts
            },
            "operations": self.operations,
            "performance": {
                "avg_response_ms": self.get_average_response(),
                "samples": len(self.response_times)
            },
            "errors": {
                "last_error": self.last_error,
                "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None
            }
        }

class Database:
    """
    MongoDB connection and Beanie ODM initialization manager.
    
    Features:
    - Connection pooling
    - Health monitoring
    - Performance tracking
    - Error handling
    """

    client: Optional[AsyncIOMotorClient] = None
    _last_health_check: Optional[datetime] = None
    _metrics = DatabaseMetrics()
    _lock = asyncio.Lock()
    _initialized = False

    @classmethod
    async def connect_db(cls) -> None:
        """
        Establish MongoDB connection with retry logic.
        
        Raises:
            ConfigurationError: If configuration is invalid
            DatabaseError: If connection fails after retries
        """
        if cls._initialized:
            return

        async with cls._lock:
            try:
                # Get settings
                settings = await cls._get_settings()
                
                if not settings.MONGODB_URL:
                    raise ConfigurationError(
                        "Missing database URL",
                        context={"settings": "MONGODB_URL"}
                    )

                max_retries = 5
                base_delay = 2

                for attempt in range(max_retries):
                    try:
                        cls._metrics.total_connections += 1
                        start_time = datetime.utcnow()
                        
                        # Create client with connection pooling
                        cls.client = AsyncIOMotorClient(
                            settings.MONGODB_URL,
                            maxPoolSize=settings.MONGODB_MAX_CONNECTIONS,
                            minPoolSize=settings.MONGODB_MIN_CONNECTIONS,
                            serverSelectionTimeoutMS=settings.MONGODB_TIMEOUT_MS,
                            connectTimeoutMS=settings.MONGODB_TIMEOUT_MS,
                            waitQueueTimeoutMS=settings.MONGODB_TIMEOUT_MS,
                            retryWrites=True,
                            retryReads=True
                        )

                        # Initialize Beanie ODM
                        await init_beanie(
                            database=cls.client[settings.MONGODB_DB_NAME],
                            document_models=await cls._get_document_models()
                        )

                        # Verify connection
                        await cls.client.admin.command("ping")
                        
                        # Track connection time
                        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                        cls._metrics.add_response_time(response_time)
                        cls._initialized = True

                        logger.info(
                            "Connected to MongoDB",
                            extra={
                                "database": settings.MONGODB_DB_NAME,
                                "max_connections": settings.MONGODB_MAX_CONNECTIONS,
                                "attempt": attempt + 1,
                                "response_time_ms": response_time,
                                "metrics": cls._metrics.to_dict()
                            }
                        )
                        return

                    except Exception as e:
                        cls._metrics.failed_connections += 1
                        cls._metrics.last_error = str(e)
                        cls._metrics.last_error_time = datetime.utcnow()
                        
                        if attempt == max_retries - 1:
                            await handle_api_error(
                                error=DatabaseError(
                                    "Failed to connect after multiple attempts",
                                    context={
                                        "attempts": max_retries,
                                        "last_error": str(e),
                                        "metrics": cls._metrics.to_dict()
                                    }
                                ),
                                context={
                                    "service": "database",
                                    "action": "connect",
                                    "attempts": max_retries
                                },
                                log_message="Database connection failed after retries"
                            )
                            raise DatabaseError(
                                "Failed to connect after multiple attempts",
                                context={
                                    "attempts": max_retries,
                                    "last_error": str(e),
                                    "metrics": cls._metrics.to_dict()
                                }
                            )

                        delay = base_delay * (2 ** attempt)
                        cls._metrics.reconnect_attempts += 1
                        
                        logger.warning(
                            "Connection attempt failed, retrying",
                            extra={
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "next_delay": delay,
                                "error": str(e),
                                "metrics": cls._metrics.to_dict()
                            }
                        )
                        await asyncio.sleep(delay)

            except ConfigurationError:
                raise
            except Exception as e:
                await handle_api_error(
                    error=e,
                    context={
                        "service": "database",
                        "action": "connect",
                        "metrics": cls._metrics.to_dict()
                    },
                    log_message="Database connection failed"
                )
                raise DatabaseError(
                    "Database connection failed",
                    context={
                        "metrics": cls._metrics.to_dict(),
                        "error": str(e)
                    }
                )

    @classmethod
    async def _get_settings(cls) -> Any:
        """Lazy load settings to avoid circular imports."""
        from app.core.config.settings import settings
        return settings

    @classmethod
    async def _get_document_models(cls) -> List[Any]:
        """Get Beanie document models."""
        from app.models.user import User
        from app.models.bot import Bot
        from app.models.account import Account
        from app.models.group import Group
        from app.models.trade import Trade
        from app.models.symbol_info import SymbolInfo
        from app.models.symbol_specs import SymbolSpecs
        from app.models.daily_performance import DailyPerformance
        from app.models.position_history import PositionHistory

        return [
            User, Bot, Account, Group, Trade,
            SymbolInfo, SymbolSpecs, DailyPerformance,
            PositionHistory
        ]

    @classmethod
    async def close_db(cls) -> None:
        """Close MongoDB connection and cleanup resources."""
        async with cls._lock:
            if cls.client is not None:
                try:
                    await cls.client.close()
                    cls.client = None
                    cls._initialized = False
                    logger.info(
                        "Closed MongoDB connection",
                        extra={"metrics": cls._metrics.to_dict()}
                    )
                except Exception as e:
                    logger.error(
                        "Error closing MongoDB connection",
                        extra={
                            "error": str(e),
                            "metrics": cls._metrics.to_dict()
                        }
                    )

    @classmethod
    async def get_db(cls) -> AsyncIOMotorClient:
        """
        Get database client with connection verification.
            
        Returns:
            AsyncIOMotorClient: Active MongoDB client
            
        Raises:
            DatabaseError: If connection cannot be established
        """
        try:
            if not cls._initialized:
                await cls.connect_db()

            start_time = datetime.utcnow()
            
            # Verify connection is still valid
            await cls.client.admin.command("ping")
            
            # Track response time
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            cls._metrics.add_response_time(response_time)
            
            return cls.client

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "service": "database",
                    "action": "get_client",
                    "metrics": cls._metrics.to_dict()
                },
                log_message="Failed to get database client"
            )
            raise DatabaseError(
                "Failed to get database client",
                context={
                    "metrics": cls._metrics.to_dict(),
                    "error": str(e)
                }
            )

    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        """
        Check database connection health.
        
        Returns:
            Dict with health status and metrics
            
        Performs:
        - Connection check
        - Server info validation 
        - Performance metrics
        """
        try:
            if not cls._initialized:
                return {
                    "healthy": False,
                    "error": "Database not initialized",
                    "metrics": cls._metrics.to_dict()
                }

            start_time = datetime.utcnow()

            # Get server info and stats
            server_info = await cls.client.server_info()
            server_status = await cls.client.admin.command("serverStatus")
            
            # Track response time
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            cls._metrics.add_response_time(response_time)

            # Update last check time
            cls._last_health_check = datetime.utcnow()

            metrics = {
                **cls._metrics.to_dict(),
                "server": {
                    "version": server_info.get("version"),
                    "uptime": server_status.get("uptime", 0),
                    "connections": server_status.get("connections", {}),
                    "operations": server_status.get("opcounters", {})
                }
            }

            logger.info(
                "Database health check passed",
                extra=metrics
            )

            return {
                "healthy": True,
                "metrics": metrics
            }

        except Exception as e:
            error_context = {
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": cls._metrics.to_dict()
            }
            
            logger.error(
                "Database health check failed",
                extra=error_context
            )
            
            return {
                "healthy": False,
                "error": str(e),
                "metrics": cls._metrics.to_dict()
            }

    @classmethod
    async def recreate_indexes(cls) -> Dict[str, Any]:
        """
        Recreate all document model indexes.
            
        Returns:
            Dict with operation results
            
        Raises:
            DatabaseError: If index creation fails
        """
        try:
            if not cls._initialized:
                await cls.connect_db()

            settings = await cls._get_settings()
            db = cls.client[settings.MONGODB_DB_NAME]
            
            # Get all models
            models = await cls._get_document_models()
            
            # Track results
            results = []
            
            for model in models:
                try:
                    collection = db[model.Settings.name]
                    
                    # Drop existing indexes
                    await collection.drop_indexes()
                    
                    # Create new indexes
                    await collection.create_indexes(model.Settings.indexes)
                    
                    results.append({
                        "model": model.__name__,
                        "success": True,
                        "indexes": len(model.Settings.indexes)
                    })
                    
                except Exception as e:
                    results.append({
                        "model": model.__name__,
                        "success": False,
                        "error": str(e)
                    })

            # Summarize results
            summary = {
                "total": len(results),
                "successful": sum(1 for r in results if r["success"]),
                "failed": sum(1 for r in results if not r["success"]),
                "results": results
            }

            logger.info(
                "Recreated database indexes",
                extra={
                    "metrics": cls._metrics.to_dict(),
                    **summary
                }
            )

            return summary

        except Exception as e:
            await handle_api_error(
                error=e,
                context={
                    "service": "database",
                    "action": "recreate_indexes",
                    "metrics": cls._metrics.to_dict()
                },
                log_message="Failed to recreate indexes"
            )
            raise DatabaseError(
                "Failed to recreate indexes",
                context={
                    "metrics": cls._metrics.to_dict(),
                    "error": str(e)
                }
            )

# Move imports to end to avoid circular imports
from app.core.errors import (
    DatabaseError,
    ValidationError,
    ConfigurationError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

# Create global instance
db = Database()