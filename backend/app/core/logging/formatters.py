"""
Log formatters for structured logging with error context and performance metrics.

Features:
- JSON structured logging
- Error context enrichment
- Performance metrics
- Request tracking
- Rich metadata formatting
"""

import logging
import json
import traceback
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum

class LogLevel(str, Enum):
    """Valid logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class ErrorLevel(str, Enum):
    """Error severity levels for log context."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging output.
    
    Features:
    - Enforces consistent log structure
    - Includes error context and stack traces
    - Adds request tracing
    - Tracks performance metrics
    - Enriches metadata
    
    Example output:
    {
        "timestamp": "2025-01-16T10:30:00.123Z",
        "level": "ERROR",
        "logger": "app.services.trading",
        "message": "Trade execution failed",
        "error": {
            "type": "ExchangeError",
            "message": "Invalid symbol",
            "level": "HIGH",
            "context": {...},
            "traceback": "..."
        },
        "request": {
            "id": "req-123",
            "method": "POST",
            "path": "/api/v1/trade"
        },
        "performance": {
            "duration_ms": 150,
            "sql_time_ms": 45
        }
    }
    """

    def __init__(self, fmt: Optional[str] = None, *args, **kwargs):
        """Initialize formatter with optional format string."""
        super().__init__(fmt, *args, **kwargs)
        self.hostname = self._get_hostname()
        self.pid = self._get_process_id()

    def _get_hostname(self) -> str:
        """Get system hostname for log context."""
        try:
            import socket
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _get_process_id(self) -> int:
        """Get current process ID for log context."""
        try:
            import os
            return os.getpid()
        except Exception:
            return -1

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON string.
        
        Args:
            record: The log record to format
            
        Returns:
            str: JSON formatted log entry
        """
        # Base log data
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": {
                "id": self.pid,
                "name": record.processName
            },
            "host": self.hostname
        }

        # Add error context if present
        if hasattr(record, "error_context"):
            error_context = record.error_context
            log_data["error"] = {
                "type": error_context.get("error_type"),
                "message": error_context.get("message"),
                "level": error_context.get("level"),
                "category": error_context.get("category"),
                "context": error_context.get("context"),
                "traceback": error_context.get("traceback")
            }

        # Add exception info
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            log_data["exception"] = {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "traceback": "".join(traceback.format_exception(*record.exc_info))
            }

        # Add request context if present
        if hasattr(record, "request_context"):
            log_data["request"] = record.request_context

        # Add performance metrics if present
        if hasattr(record, "performance"):
            log_data["performance"] = record.performance

        # Add custom fields from extra
        if hasattr(record, "extra_fields"):
            for key, value in record.extra_fields.items():
                if key not in log_data:
                    log_data[key] = value

        return json.dumps(log_data, default=self._json_serializer)

    def _json_serializer(self, obj: Any) -> Any:
        """
        Custom JSON serializer for complex objects.
        
        Handles:
        - datetime objects
        - Enum values
        - Decimal values
        - Custom objects with to_dict()
        
        Args:
            obj: Object to serialize
            
        Returns:
            JSON serializable value
        """
        # Handle datetime objects
        if isinstance(obj, datetime):
            return obj.isoformat()

        # Handle Enum values
        if isinstance(obj, Enum):
            return obj.value

        # Handle Decimal values
        if hasattr(obj, "as_integer_ratio"):
            return float(obj)

        # Handle objects with to_dict method
        if hasattr(obj, "to_dict"):
            return obj.to_dict()

        # Default serialization
        try:
            return str(obj)
        except Exception:
            return None

class TextFormatter(logging.Formatter):
    """
    Human-readable text formatter for development and debugging.
    
    Features:
    - Clean, readable output format
    - Colored output based on level
    - Includes relevant context
    - Shows performance metrics
    
    Example output:
    2025-01-16 10:30:00.123 ERROR [app.services.trading] Trade execution failed
    Error: ExchangeError (HIGH)
    Message: Invalid symbol
    Context: {"symbol": "BTC-USD", "exchange": "okx"}
    Request: POST /api/v1/trade (req-123)
    Duration: 150ms (SQL: 45ms)
    </code>
    """

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
        "RESET": "\033[0m"        # Reset
    }

    def __init__(
        self,
        fmt: Optional[str] = None,
        use_colors: bool = True,
        *args,
        **kwargs
    ):
        """
        Initialize text formatter.
        
        Args:
            fmt: Optional format string
            use_colors: Whether to use ANSI colors
        """
        super().__init__(fmt, *args, **kwargs)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as human-readable text."""
        # Start with timestamp and level
        color = self.COLORS.get(record.levelname, "") if self.use_colors else ""
        reset = self.COLORS["RESET"] if self.use_colors else ""
        
        output = [
            f"{self.formatTime(record)} "
            f"{color}{record.levelname}{reset} "
            f"[{record.name}] {record.getMessage()}"
        ]

        # Add error context
        if hasattr(record, "error_context"):
            error = record.error_context
            output.extend([
                f"Error: {error.get("error_type")} ({error.get("level", "UNKNOWN")})",
                f"Message: {error.get("message")}",
                f"Context: {json.dumps(error.get("context", {}))}"
            ])
            if error.get("traceback"):
                output.append(f"Traceback:\n{error["traceback"]}")

        # Add exception info
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            output.extend([
                f"Exception: {exc_type.__name__}",
                f"Message: {str(exc_value)}",
                "Traceback:",
                "".join(traceback.format_exception(*record.exc_info))
            ])

        # Add request context
        if hasattr(record, "request_context"):
            req = record.request_context
            output.append(
                f"Request: {req.get("method", "UNKNOWN")} {req.get("path", "UNKNOWN")} "
                f"({req.get("id", "UNKNOWN")})"
            )

        # Add performance metrics
        if hasattr(record, "performance"):
            perf = record.performance
            metrics = [
                f"{k}: {v}ms" for k, v in perf.items()
                if isinstance(v, (int, float))
            ]
            if metrics:
                output.append("Performance: " + ", ".join(metrics))

        # Add extra fields
        if hasattr(record, "extra_fields"):
            for key, value in record.extra_fields.items():
                output.append(f"{key}: {value}")

        return "\n".join(output)

def create_formatter(
    fmt_type: str = "json",
    use_colors: bool = True,
    fmt_string: Optional[str] = None
) -> logging.Formatter:
    """
    Create appropriate formatter based on type.
    
    Args:
        fmt_type: "json" or "text"
        use_colors: Whether to use colors in text output
        fmt_string: Optional format string
        
    Returns:
        logging.Formatter: Configured formatter instance
        
    Raises:
        ValueError: If invalid format type
    """
    if fmt_type == "json":
        return JSONFormatter(fmt=fmt_string)
    elif fmt_type == "text":
        return TextFormatter(fmt=fmt_string, use_colors=use_colors)
    else:
        raise ValueError(f"Invalid formatter type: {fmt_type}")