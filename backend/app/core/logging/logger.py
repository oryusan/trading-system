import sys
import os
import logging
import logging.handlers
import traceback
import threading
import queue
import atexit
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Protocol, List, Union

from app.core.errors.base import BaseError
from app.core.config import settings
from .formatters import create_formatter


class LogProtocol(Protocol):
    """Protocol defining the async logging interface."""
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


class AsyncLogHandler(logging.Handler):
    """
    A handler that dispatches log records to a separate thread
    to avoid blocking the asyncio event loop.
    """
    def __init__(self, capacity: int = 10000):
        """
        Initialize with a queue to hold log records.
        
        Args:
            capacity: Maximum number of records to queue before blocking
        """
        super().__init__()
        self.queue = queue.Queue(capacity)
        self.handlers: List[logging.Handler] = []
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._process_logs, daemon=True)
        self._worker.start()
        atexit.register(self.close)
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Add the record to the queue for asynchronous processing.
        
        Args:
            record: The log record to process
        """
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # If queue is full, fall back to immediate processing
            self._process_record(record)
    
    def _process_logs(self) -> None:
        """Worker thread that processes logs from the queue."""
        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                record = self.queue.get(block=True, timeout=0.2)
                self._process_record(record)
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                # Ensure the thread doesn't die on exception
                continue
    
    def _process_record(self, record: logging.LogRecord) -> None:
        """Process a single log record by dispatching to handlers."""
        for handler in self.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def close(self) -> None:
        """
        Clean up resources when shutting down.
        Ensures all queued messages are processed.
        """
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)  # Wait up to 5 seconds
        
        # Close all handlers
        for handler in self.handlers:
            handler.close()
        
        super().close()
    
    def add_handler(self, handler: logging.Handler) -> None:
        """
        Add a handler to receive log records.
        
        Args:
            handler: The handler to add
        """
        self.handlers.append(handler)


class AsyncLogger(LogProtocol):
    """
    Async-friendly logger implementation that doesn't block the event loop.
    Uses a non-blocking queue and worker thread for log processing.
    """

    # Logger cache to avoid creating multiple instances for the same name
    _loggers: Dict[str, 'AsyncLogger'] = {}
    # Single async handler shared across loggers
    _async_handler: Optional[AsyncLogHandler] = None
    # Lock for thread-safe initialization
    _init_lock = threading.Lock()

    def __init__(self, name: str) -> None:
        """
        Initialize a new logger.
        
        Args:
            name: The logger name, typically the module name
        """
        self.name = name
        self.logger = logging.getLogger(name)
        
        # Configure the logger only on first initialization
        with self._init_lock:
            if not AsyncLogger._async_handler:
                AsyncLogger._configure_async_handler()
            
            # Set the logger's level and handler if not already done
            if not self.logger.handlers:
                self.logger.addHandler(AsyncLogger._async_handler)
                self.logger.setLevel(self._get_log_level())
                # Prevent propagation to avoid duplicate logs
                self.logger.propagate = False

    @classmethod
    def _configure_async_handler(cls) -> None:
        """Configure the shared async handler and all output handlers."""
        # Create async handler with sufficient capacity
        cls._async_handler = AsyncLogHandler(capacity=50000)
        
        # Ensure logs directory exists
        log_dir = Path(os.path.dirname(settings.logging.LOG_FILE_PATH))
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine appropriate formatter based on settings
        log_format = settings.logging.LOG_FORMAT.lower()
        use_colors = settings.logging.USE_COLORS
        formatter = create_formatter(fmt_type=log_format, use_colors=use_colors)
        
        # Create handlers based on configuration
        handlers = []
        
        # File handler for main logs with rotation
        file_handler = logging.handlers.RotatingFileHandler(
            settings.logging.LOG_FILE_PATH,
            maxBytes=settings.logging.MAX_LOG_SIZE,
            backupCount=settings.logging.MAX_LOG_BACKUPS,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(cls._get_log_level())
        handlers.append(file_handler)
        
        # File handler for errors with rotation
        error_handler = logging.handlers.RotatingFileHandler(
            settings.logging.ERROR_LOG_FILE_PATH,
            maxBytes=settings.logging.MAX_LOG_SIZE,
            backupCount=settings.logging.MAX_LOG_BACKUPS,
            encoding='utf-8'
        )
        error_handler.setFormatter(formatter)
        error_handler.setLevel(logging.ERROR)
        handlers.append(error_handler)
        
        # Console handler if enabled
        if settings.logging.CONSOLE_LOGGING:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(cls._get_log_level())
            handlers.append(console_handler)
        
        # Add all handlers to the async handler
        for handler in handlers:
            cls._async_handler.add_handler(handler)

    @staticmethod
    def _get_log_level() -> int:
        """Get the log level from settings as an int."""
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        level_str = settings.logging.LOG_LEVEL.value
        return level_map.get(level_str, logging.INFO)

    def _build_error_context(
        self, error: Exception, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Return a dictionary with error details and context.
        
        Args:
            error: The exception to process
            context: Additional context for the error
            
        Returns:
            A dictionary with error details and context
        """
        error_context = {
            "error_type": type(error).__name__,
            "message": str(error),
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat(),
            "environment": settings.app.ENVIRONMENT,
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
        """
        Log an error asynchronously with enriched error context.
        
        Args:
            error: The exception to log
            context: Additional context for the error
            message: Optional message to include
        """
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
        """
        Log a critical error asynchronously.
        
        Args:
            message: The message to log
            error: Optional exception to include
            context: Additional context
        """
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
        """
        Log performance metrics asynchronously.
        
        Args:
            operation: The operation being measured
            duration: Duration in milliseconds
            context: Additional context
        """
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


@lru_cache(maxsize=100)
def get_logger(name: str) -> AsyncLogger:
    """
    Return a cached AsyncLogger instance for the given name.
    Uses LRU cache to avoid creating duplicate loggers.
    
    Args:
        name: The logger name, typically the module name
        
    Returns:
        An AsyncLogger instance
    """
    if name not in AsyncLogger._loggers:
        AsyncLogger._loggers[name] = AsyncLogger(name)
    return AsyncLogger._loggers[name]


def init_logging() -> None:
    """
    Initialize the logging system.
    This ensures the logs directory exists and configures the root logger.
    """
    # Create the logs directory
    log_dir = Path(os.path.dirname(settings.logging.LOG_FILE_PATH))
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize a logger to trigger the async handler setup
    get_logger("app")


def cleanup_logging() -> None:
    """
    Clean up logging resources.
    Clears the logger cache and properly closes all handlers.
    """
    # Clear the cache
    get_logger.cache_clear()
    
    # Close the async handler
    if AsyncLogger._async_handler:
        AsyncLogger._async_handler.close()
    
    # Reset class variables
    AsyncLogger._loggers.clear()
    AsyncLogger._async_handler = None
    
    # Close handlers on the root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)


def configure_log_levels(levels: Dict[str, Union[str, int]]) -> None:
    """
    Configure log levels for specific loggers.
    Useful for adjusting verbosity of third-party libraries.
    
    Args:
        levels: Dictionary mapping logger names to levels
    """
    for logger_name, level in levels.items():
        logger = logging.getLogger(logger_name)
        if isinstance(level, str):
            level_map = {
                'DEBUG': logging.DEBUG,
                'INFO': logging.INFO,
                'WARNING': logging.WARNING,
                'ERROR': logging.ERROR,
                'CRITICAL': logging.CRITICAL
            }
            level = level_map.get(level.upper(), logging.INFO)
        logger.setLevel(level)