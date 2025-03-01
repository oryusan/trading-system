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

import asyncio
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any, List

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.core.errors.base import DatabaseError, ValidationError, ConfigurationError
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class DatabaseMetrics:
    """Track database metrics and performance."""

    def __init__(self) -> None:
        self.total_connections: int = 0
        self.failed_connections: int = 0
        self.reconnect_attempts: int = 0
        self.operations: Dict[str, int] = {"reads": 0, "writes": 0, "queries": 0}
        self.response_times: deque = deque(maxlen=100)
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[datetime] = None

    def add_response_time(self, time_ms: float) -> None:
        """Add a new response time measurement."""
        self.response_times.append(time_ms)

    def get_average_response(self) -> float:
        """Calculate the average response time."""
        return sum(self.response_times) / len(self.response_times) if self.response_times else 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a dictionary."""
        return {
            "connections": {
                "total": self.total_connections,
                "failed": self.failed_connections,
                "reconnects": self.reconnect_attempts,
            },
            "operations": self.operations,
            "performance": {
                "avg_response_ms": self.get_average_response(),
                "samples": len(self.response_times),
            },
            "errors": {
                "last_error": self.last_error,
                "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
            },
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
    _initialized: bool = False
    _lock = asyncio.Lock()
    _metrics = DatabaseMetrics()
    _last_health_check: Optional[datetime] = None

    @classmethod
    async def connect_db(cls) -> None:
        """
        Establish MongoDB connection with retry logic.

        Raises:
            ConfigurationError: If configuration is invalid.
            DatabaseError: If connection fails after retries.
        """
        if cls._initialized:
            return

        async with cls._lock:
            settings = await cls._get_settings()
            if not settings.database.MONGODB_URL:
                raise ConfigurationError(
                    "Missing database URL", context={"settings": "MONGODB_URL"}
                )

            max_retries = 5
            base_delay = 2

            for attempt in range(max_retries):
                try:
                    await cls._attempt_connection(settings, attempt)
                    return  # Successful connection.
                except Exception as e:
                    cls._metrics.failed_connections += 1
                    if attempt == max_retries - 1:
                        error_context = {
                            "attempts": max_retries,
                            "last_error": str(e),
                            "metrics": cls._metrics.to_dict(),
                        }
                        await handle_api_error(
                            error=DatabaseError("Failed to connect after multiple attempts", context=error_context),
                            context={"service": "database", "action": "connect", "attempts": max_retries},
                            log_message="Database connection failed after retries",
                        )
                        raise DatabaseError("Failed to connect after multiple attempts", context=error_context) from e

                    delay = base_delay * (2 ** attempt)
                    cls._metrics.reconnect_attempts += 1
                    logger.warning(
                        "Connection attempt failed, retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "next_delay": delay,
                            "error": str(e),
                            "metrics": cls._metrics.to_dict(),
                        },
                    )
                    await asyncio.sleep(delay)

    @classmethod
    async def _attempt_connection(cls, settings: Any, attempt: int) -> None:
        """Attempt a single connection to MongoDB."""
        cls._metrics.total_connections += 1
        start_time = datetime.utcnow()

        cls.client = AsyncIOMotorClient(
            settings.database.MONGODB_URL,
            maxPoolSize=settings.database.MONGODB_MAX_CONNECTIONS,
            minPoolSize=settings.database.MONGODB_MIN_CONNECTIONS,
            serverSelectionTimeoutMS=settings.database.MONGODB_TIMEOUT_MS,
            connectTimeoutMS=settings.database.MONGODB_TIMEOUT_MS,
            waitQueueTimeoutMS=settings.database.MONGODB_TIMEOUT_MS,
            retryWrites=True,
            retryReads=True,
        )

        await init_beanie(
            database=cls.client[settings.database.MONGODB_DB_NAME],
            document_models=await cls._get_document_models(),
        )

        # Verify connection with a ping.
        await cls.client.admin.command("ping")

        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        cls._metrics.add_response_time(response_time)
        cls._initialized = True

        logger.info(
            "Connected to MongoDB",
            extra={
                "database": settings.database.MONGODB_DB_NAME,
                "max_connections": settings.database.MONGODB_MAX_CONNECTIONS,
                "attempt": attempt + 1,
                "response_time_ms": response_time,
                "metrics": cls._metrics.to_dict(),
            },
        )

    @classmethod
    async def _get_settings(cls) -> Any:
        """Lazy load settings to avoid circular imports."""
        from app.core.config.settings import settings
        return settings

    @classmethod
    async def _get_document_models(cls) -> List[Any]:
        """Get Beanie document models."""
        from app.models.entities.user import User
        from app.models.entities.bot import Bot
        from app.models.entities.account import Account
        from app.models.entities.group import AccountGroup
        from app.models.entities.trade import Trade
        from app.models.entities.symbol_data import SymbolData
        from app.models.entities.daily_performance import DailyPerformance
        from app.models.entities.position_history import PositionHistory

        return [
            User, Bot, Account, AccountGroup, Trade,
            SymbolData, DailyPerformance,
            PositionHistory,
        ]

    @classmethod
    async def close_db(cls) -> None:
        """Close MongoDB connection and clean up resources."""
        async with cls._lock:
            if cls.client is not None:
                try:
                    await cls.client.close()
                    cls.client = None
                    cls._initialized = False
                    logger.info("Closed MongoDB connection", extra={"metrics": cls._metrics.to_dict()})
                except Exception as e:
                    logger.error(
                        "Error closing MongoDB connection",
                        extra={"error": str(e), "metrics": cls._metrics.to_dict()},
                    )

    @classmethod
    async def get_db(cls) -> AsyncIOMotorClient:
        """
        Get the database client with connection verification.

        Returns:
            AsyncIOMotorClient: Active MongoDB client.

        Raises:
            DatabaseError: If the connection cannot be established.
        """
        if not cls._initialized:
            await cls.connect_db()

        try:
            start_time = datetime.utcnow()
            await cls.client.admin.command("ping")
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            cls._metrics.add_response_time(response_time)
            return cls.client
        except Exception as e:
            error_context = {"metrics": cls._metrics.to_dict(), "error": str(e)}
            await handle_api_error(
                error=e,
                context={"service": "database", "action": "get_client", "metrics": cls._metrics.to_dict()},
                log_message="Failed to get database client",
            )
            raise DatabaseError("Failed to get database client", context=error_context) from e

    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        """
        Check database connection health.

        Returns:
            A dict with health status and metrics.
        """
        if not cls._initialized:
            return {
                "healthy": False,
                "error": "Database not initialized",
                "metrics": cls._metrics.to_dict(),
            }
        try:
            start_time = datetime.utcnow()
            server_info, server_status = await asyncio.gather(
                cls.client.server_info(),
                cls.client.admin.command("serverStatus"),
            )
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            cls._metrics.add_response_time(response_time)
            cls._last_health_check = datetime.utcnow()

            metrics = {
                **cls._metrics.to_dict(),
                "server": {
                    "version": server_info.get("version"),
                    "uptime": server_status.get("uptime", 0),
                    "connections": server_status.get("connections", {}),
                    "operations": server_status.get("opcounters", {}),
                },
            }

            logger.info("Database health check passed", extra=metrics)
            return {"healthy": True, "metrics": metrics}
        except Exception as e:
            error_context = {
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": cls._metrics.to_dict(),
            }
            logger.error("Database health check failed", extra=error_context)
            return {"healthy": False, "error": str(e), "metrics": cls._metrics.to_dict()}

    @classmethod
    async def recreate_indexes(cls) -> Dict[str, Any]:
        """
        Recreate all document model indexes.

        Returns:
            A dict with operation results.

        Raises:
            DatabaseError: If index creation fails.
        """
        try:
            if not cls._initialized:
                await cls.connect_db()

            settings = await cls._get_settings()
            db_instance = cls.client[settings.database.MONGODB_DB_NAME]
            models = await cls._get_document_models()

            # Run index recreation concurrently for all models.
            results = await asyncio.gather(
                *[cls._recreate_indexes_for_model(model, db_instance) for model in models]
            )

            summary = {
                "total": len(results),
                "successful": sum(1 for r in results if r.get("success")),
                "failed": sum(1 for r in results if not r.get("success")),
                "results": results,
            }

            logger.info("Recreated database indexes", extra={**summary, "metrics": cls._metrics.to_dict()})
            return summary
        except Exception as e:
            error_context = {"metrics": cls._metrics.to_dict(), "error": str(e)}
            await handle_api_error(
                error=e,
                context={"service": "database", "action": "recreate_indexes", "metrics": cls._metrics.to_dict()},
                log_message="Failed to recreate indexes",
            )
            raise DatabaseError("Failed to recreate indexes", context=error_context) from e

    @classmethod
    async def _recreate_indexes_for_model(cls, model: Any, db_instance: Any) -> Dict[str, Any]:
        """Helper method to recreate indexes for a single model."""
        try:
            collection = db_instance[model.Settings.name]
            await collection.drop_indexes()
            await collection.create_indexes(model.Settings.indexes)
            return {"model": model.__name__, "success": True, "indexes": len(model.Settings.indexes)}
        except Exception as e:
            return {"model": model.__name__, "success": False, "error": str(e)}


# Global database instance for application-wide use.
db = Database()
