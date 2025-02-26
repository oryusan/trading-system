import sys
import logging
import traceback
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Protocol

from app.core.errors.base import BaseError
from .formatters import JSONFormatter


class LogProtocol(Protocol):
    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None:
        ...

    async def log_critical(
        self,
        message: str,
        error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        ...

    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        ...


class AsyncLogger(LogProtocol):
    """Async-friendly logger implementation."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger = logging.getLogger(name)
        self._configure_logger()

    def _configure_logger(self) -> None:
        """Configure the logger only once by adding file and console handlers."""
        if self.logger.handlers:
            return

        # Ensure the logs directory exists.
        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)

        json_formatter = JSONFormatter()

        # Define a list of (handler, level) tuples.
        handlers = [
            (logging.FileHandler(logs_dir / "app.log"), logging.INFO),
            (logging.FileHandler(logs_dir / "error.log"), logging.ERROR),
            (logging.StreamHandler(sys.stdout), logging.INFO),
        ]

        for handler, level in handlers:
            handler.setFormatter(json_formatter)
            handler.setLevel(level)
            self.logger.addHandler(handler)

        self.logger.setLevel(logging.INFO)

    def _build_error_context(
        self, error: Exception, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return a dictionary with error details and context."""
        error_context = {
            "error_type": type(error).__name__,
            "message": str(error),
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
        if isinstance(error, BaseError):
            error_context.update({
                "category": error.category,
                "level": error.level,
                "traceback": error.traceback,
            })
        else:
            error_context["traceback"] = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        return error_context

    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None:
        """Log an error asynchronously with enriched error context."""
        error_context = self._build_error_context(error, context)
        extra = {
            "error_context": error_context,
            "request_context": context.get("request") if context else None,
        }
        self.logger.error(message or str(error), extra=extra)

    async def log_critical(
        self,
        message: str,
        error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log a critical error asynchronously."""
        if error:
            await self.log_error(error, context=context, message=message)
        else:
            self.logger.critical(message, extra={"context": context})

    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log performance metrics asynchronously."""
        performance_data = {
            "operation": operation,
            "duration": duration,
            "timestamp": datetime.utcnow().isoformat(),
            **(context or {}),
        }
        self.logger.info(f"Performance: {operation}", extra={"performance": performance_data})

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug message."""
        self.logger.debug(message, extra=kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message."""
        self.logger.info(message, extra=kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message."""
        self.logger.warning(message, extra=kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message."""
        self.logger.error(message, extra=kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log a critical message."""
        self.logger.critical(message, extra=kwargs)


@lru_cache()
def get_logger(name: str) -> AsyncLogger:
    """
    Return a cached AsyncLogger instance for the given name.
    """
    return AsyncLogger(name)


def init_logging() -> None:
    """
    Initialize the logging system by ensuring the logs directory exists.
    """
    Path("logs").mkdir(parents=True, exist_ok=True)


def cleanup_logging() -> None:
    """
    Clean up logging resources by clearing the logger cache and closing all handlers.
    """
    get_logger.cache_clear()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
