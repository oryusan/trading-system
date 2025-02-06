"""
Enhanced logging system with structured error handling integration.

Features:
- Async-friendly logging
- Structured JSON logging
- Error context enrichment
- Performance metrics
- Request tracking
"""

import sys
import json
import logging
import traceback
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Union, Protocol
import asyncio

from app.core.errors.base import BaseError, ErrorLevel, ErrorCategory

class LogProtocol(Protocol):
    """Protocol defining required logging interface."""
    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None: ...
    
    async def log_critical(
        self,
        message: str,
        error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> None: ...
    
    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None: ...

class JSONFormatter(logging.Formatter):
    """Enhanced JSON formatter with error context support."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with error context."""
        log_data = {
            "timestamp": self.formatTime(record),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # Add error context if present
        if hasattr(record, "error_context"):
            log_data["error"] = {
                "type": record.error_context.get("error_type"),
                "category": record.error_context.get("category"),
                "level": record.error_context.get("level"),
                "context": record.error_context.get("context"),
                "traceback": record.error_context.get("traceback")
            }

        # Add request context if present
        if hasattr(record, "request_context"):
            log_data["request"] = record.request_context

        # Add performance metrics if present
        if hasattr(record, "performance"):
            log_data["performance"] = record.performance

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info)
            }

        return json.dumps(log_data)

class AsyncLogger(LogProtocol):
    """Async-friendly logger implementation."""
    
    def __init__(self, name: str):
        """Initialize logger with name."""
        self.name = name
        self._setup_logger()

    def _setup_logger(self) -> None:
        """Set up logging configuration."""
        self.logger = logging.getLogger(self.name)
        
        # Ensure logs directory exists
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        
        # Create handlers
        file_handler = logging.FileHandler(logs_dir / "app.log")
        error_handler = logging.FileHandler(logs_dir / "error.log")
        console_handler = logging.StreamHandler(sys.stdout)
        
        # Set formatters
        json_formatter = JSONFormatter()
        file_handler.setFormatter(json_formatter)
        error_handler.setFormatter(json_formatter)
        console_handler.setFormatter(json_formatter)
        
        # Set levels
        error_handler.setLevel(logging.ERROR)
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(error_handler)
        self.logger.addHandler(console_handler)
        
        # Set base level
        self.logger.setLevel(logging.INFO)

    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None:
        """Log error with context."""
        error_context = {
            "error_type": error.__class__.__name__,
            "message": str(error),
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat()
        }

        if isinstance(error, BaseError):
            error_context.update({
                "category": error.category,
                "level": error.level,
                "traceback": error.traceback
            })
        else:
            error_context["traceback"] = "".join(
                traceback.format_exception(
                    type(error),
                    error,
                    error.__traceback__
                )
            )

        self.logger.error(
            message or str(error),
            extra={
                "error_context": error_context,
                "request_context": context.get("request") if context else None
            }
        )

    async def log_critical(
        self,
        message: str,
        error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log critical error."""
        if error:
            await self.log_error(
                error,
                context=context,
                message=message
            )
        else:
            self.logger.critical(
                message,
                extra={"context": context}
            )

    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log performance metrics."""
        performance_data = {
            "operation": operation,
            "duration": duration,
            "timestamp": datetime.utcnow().isoformat()
        }
        if context:
            performance_data.update(context)

        self.logger.info(
            f"Performance: {operation}",
            extra={"performance": performance_data}
        )

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self.logger.debug(message, extra=kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self.logger.info(message, extra=kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self.logger.warning(message, extra=kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self.logger.error(message, extra=kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log critical message."""
        self.logger.critical(message, extra=kwargs)

@lru_cache()
def get_logger(name: str) -> AsyncLogger:
    """Get cached logger instance."""
    return AsyncLogger(name)

def init_logging() -> None:
    """Initialize logging system."""
    # Ensure logs directory exists
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

def cleanup_logging() -> None:
    """Clean up logging resources."""
    # Clear logger cache
    get_logger.cache_clear()
    
    # Close handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:  # Slice copy to avoid modification during iteration
        handler.close()
        root_logger.removeHandler(handler)