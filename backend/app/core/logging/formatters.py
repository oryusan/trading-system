"""
Log formatters for structured logging with error context and performance metrics.

Features:
- JSON structured logging
- Error context enrichment
- Performance metrics
- Request tracking
- Rich metadata formatting
- Optimized for performance
"""

import logging
import json
import traceback
import os
import socket
import time
from datetime import datetime
from typing import Any, Optional, Dict, List, Set, Union
from enum import Enum

# Define the set of standard LogRecord attributes so we can merge extra fields.
STANDARD_LOG_ATTRS: Set[str] = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
    "process", "message",
}


class BaseLogFormatter(logging.Formatter):
    """
    Base class for common log record extraction logic.
    """

    def get_error_context(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        """
        Extract error context from a log record.
        
        Args:
            record: The log record to process
            
        Returns:
            Error context dictionary or None
        """
        error = getattr(record, "error_context", None)
        if error:
            return {
                "type": error.get("error_type"),
                "message": error.get("message"),
                "level": error.get("level"),
                "category": error.get("category"),
                "context": error.get("context"),
                "traceback": self._truncate_traceback(error.get("traceback", "")),
            }
        return None

    def _truncate_traceback(self, tb: str, max_lines: int = 20) -> str:
        """
        Truncate a traceback to a reasonable size.
        
        Args:
            tb: The traceback string
            max_lines: Maximum number of lines to keep
            
        Returns:
            Truncated traceback
        """
        if not tb:
            return ""
            
        lines = tb.splitlines()
        if len(lines) <= max_lines:
            return tb
            
        # Keep the first few and last few lines for context
        preserved_lines = max_lines - 3  # -3 for the ellipsis line
        first_chunk = preserved_lines // 2
        last_chunk = preserved_lines - first_chunk
        
        truncated = lines[:first_chunk]
        truncated.append(f"... [{len(lines) - preserved_lines} lines truncated] ...")
        truncated.extend(lines[-last_chunk:])
        
        return "\n".join(truncated)

    def get_exception_info(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        """
        Extract exception information from a log record.
        
        Args:
            record: The log record to process
            
        Returns:
            Exception information dictionary or None
        """
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            return {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "traceback": self._truncate_traceback(
                    "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                ),
            }
        return None

    def get_request_context(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        """
        Extract request context from a log record.
        
        Args:
            record: The log record to process
            
        Returns:
            Request context dictionary or None
        """
        return getattr(record, "request_context", None)

    def get_performance_info(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        """
        Extract performance information from a log record.
        
        Args:
            record: The log record to process
            
        Returns:
            Performance information dictionary or None
        """
        return getattr(record, "performance", None)

    def get_extra_fields(
        self, record: logging.LogRecord, base: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract extra fields from a log record.
        
        Args:
            record: The log record to process
            base: Base dictionary to exclude fields from
            
        Returns:
            Dictionary of extra fields
        """
        if base is None:
            base = {}
            
        # Use dictionary comprehension for better performance
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_LOG_ATTRS and key not in base
        }


class JSONFormatter(BaseLogFormatter):
    """
    JSON formatter that outputs log records as JSON strings.
    It adds extra context such as hostname and process ID.
    Optimized for performance.
    """

    def __init__(self, fmt: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the JSON formatter.
        
        Args:
            fmt: Optional format string
            *args: Additional arguments for the base formatter
            **kwargs: Additional keyword arguments for the base formatter
        """
        super().__init__(fmt, *args, **kwargs)
        self.hostname = self._get_hostname()
        self.pid = self._get_process_id()
        # Cache for datetime formatting
        self._datetime_cache: Dict[int, str] = {}
        self._cache_timestamp = 0

    def _get_hostname(self) -> str:
        """Get the hostname of the current machine."""
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _get_process_id(self) -> int:
        """Get the process ID of the current process."""
        try:
            return os.getpid()
        except Exception:
            return -1

    def _base_log_data(self, record: logging.LogRecord) -> Dict[str, Any]:
        """
        Create the base log data dictionary.
        
        Args:
            record: The log record to process
            
        Returns:
            Base log data dictionary
        """
        # Use cached datetime string for improved performance
        timestamp = self._get_formatted_time(record)
        
        return {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": {"id": self.pid, "name": record.processName},
            "host": self.hostname,
        }

    def _get_formatted_time(self, record: logging.LogRecord) -> str:
        """
        Get a formatted timestamp string for the record.
        Uses caching to improve performance.
        
        Args:
            record: The log record
            
        Returns:
            Formatted timestamp string
        """
        # Only update cache every second for performance
        current_ts = int(time.time())
        if current_ts != self._cache_timestamp or record.created not in self._datetime_cache:
            dt = datetime.fromtimestamp(record.created)
            self._datetime_cache[record.created] = dt.isoformat(timespec='milliseconds')
            
            # Clear cache when it gets too large or when second changes
            if current_ts != self._cache_timestamp:
                if len(self._datetime_cache) > 1000:
                    self._datetime_cache.clear()
                self._cache_timestamp = current_ts
                
        return self._datetime_cache[record.created]

    def _json_serializer(self, obj: Any) -> Any:
        """
        Custom JSON serializer for handling various types.
        
        Args:
            obj: The object to serialize
            
        Returns:
            JSON-serializable representation
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, Exception):
            return str(obj)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        try:
            return str(obj)
        except Exception:
            return None

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as a JSON string.
        
        Args:
            record: The log record to format
            
        Returns:
            JSON string representation of the log record
        """
        log_data = self._base_log_data(record)
        
        # Only process what's needed
        if error_ctx := self.get_error_context(record):
            log_data["error"] = error_ctx
        if exc := self.get_exception_info(record):
            log_data["exception"] = exc
        if req_ctx := self.get_request_context(record):
            log_data["request"] = req_ctx
        if perf := self.get_performance_info(record):
            log_data["performance"] = perf
            
        # Add extra fields
        log_data.update(self.get_extra_fields(record, log_data))
        
        # Serialize to JSON
        try:
            return json.dumps(log_data, default=self._json_serializer)
        except Exception as e:
            # Fallback for serialization errors
            return json.dumps({
                "timestamp": self._get_formatted_time(record),
                "level": "ERROR",
                "logger": "JSONFormatter",
                "message": f"Failed to serialize log: {e}",
                "original_message": record.getMessage()
            })


class TextFormatter(BaseLogFormatter):
    """
    Simple text formatter for human-readable logging.
    Optionally applies colors to different log levels.
    """

    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
        "RESET": "\033[0m",       # Reset
    }

    def __init__(self, fmt: Optional[str] = None, use_colors: bool = True, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the text formatter.
        
        Args:
            fmt: Optional format string
            use_colors: Whether to use colors in the output
            *args: Additional arguments for the base formatter
            **kwargs: Additional keyword arguments for the base formatter
        """
        super().__init__(fmt, *args, **kwargs)
        self.use_colors = use_colors and os.name != 'nt'  # Disable colors on Windows by default

    def _format_error_context(self, record: logging.LogRecord) -> List[str]:
        """
        Format error context as text lines.
        
        Args:
            record: The log record to format
            
        Returns:
            List of formatted text lines
        """
        lines = []
        error = self.get_error_context(record)
        if error:
            lines.append(f"Error: {error.get('type')} ({error.get('level', 'UNKNOWN')})")
            lines.append(f"Message: {error.get('message')}")
            if ctx := error.get("context"):
                # Format context as key-value pairs for readability
                ctx_str = ", ".join(f"{k}={v}" for k, v in ctx.items())
                lines.append(f"Context: {ctx_str}")
            if tb := error.get("traceback"):
                lines.append(f"Traceback:\n{tb}")
        return lines

    def _format_exception(self, record: logging.LogRecord) -> List[str]:
        """
        Format exception information as text lines.
        
        Args:
            record: The log record to format
            
        Returns:
            List of formatted text lines
        """
        lines = []
        exc = self.get_exception_info(record)
        if exc:
            lines.append(f"Exception: {exc.get('type')}")
            lines.append(f"Message: {exc.get('message')}")
            if tb := exc.get("traceback"):
                lines.append("Traceback:")
                lines.append(exc.get("traceback", ""))
        return lines

    def _format_request_context(self, record: logging.LogRecord) -> List[str]:
        """
        Format request context as text lines.
        
        Args:
            record: The log record to format
            
        Returns:
            List of formatted text lines
        """
        lines = []
        req = self.get_request_context(record)
        if req:
            lines.append(
                f"Request: {req.get('method', 'UNKNOWN')} {req.get('path', 'UNKNOWN')} ({req.get('id', 'UNKNOWN')})"
            )
            if 'client' in req:
                lines.append(f"Client: {req.get('client')}")
            if 'user' in req:
                lines.append(f"User: {req.get('user')}")
        return lines

    def _format_performance(self, record: logging.LogRecord) -> List[str]:
        """
        Format performance information as text lines.
        
        Args:
            record: The log record to format
            
        Returns:
            List of formatted text lines
        """
        lines = []
        performance = self.get_performance_info(record)
        if performance:
            metrics = []
            for k, v in performance.items():
                if isinstance(v, (int, float)):
                    unit = "ms" if k in ("duration", "time", "elapsed") else ""
                    metrics.append(f"{k}: {v}{unit}")
            if metrics:
                lines.append("Performance: " + ", ".join(metrics))
        return lines

    def _format_extra_fields(self, record: logging.LogRecord) -> List[str]:
        """
        Format extra fields as text lines.
        
        Args:
            record: The log record to format
            
        Returns:
            List of formatted text lines
        """
        extras = self.get_extra_fields(record)
        # Exclude some fields for cleaner output
        exclude = {'error_context', 'request_context', 'performance'}
        extras = {k: v for k, v in extras.items() if k not in exclude}
        
        if not extras:
            return []
            
        # Format as individual lines for readability
        return [f"{key}: {value}" for key, value in extras.items()]

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as text.
        
        Args:
            record: The log record to format
            
        Returns:
            Formatted text
        """
        color = self.COLORS.get(record.levelname, "") if self.use_colors else ""
        reset = self.COLORS["RESET"] if self.use_colors else ""
        
        # Format timestamp
        timestamp = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Create header with basic information
        header = f"{timestamp} {color}{record.levelname}{reset} [{record.name}] {record.getMessage()}"
        
        # Start with the header
        parts = [header]
        
        # Add error context if present
        parts.extend(self._format_error_context(record))
        
        # Add exception information if present
        parts.extend(self._format_exception(record))
        
        # Add request context if present
        parts.extend(self._format_request_context(record))
        
        # Add performance information if present
        parts.extend(self._format_performance(record))
        
        # Add extra fields if present
        parts.extend(self._format_extra_fields(record))
        
        # Join all parts with newlines
        return "\n".join(parts)


class CompactFormatter(BaseLogFormatter):
    """
    Compact formatter for high-volume logging.
    Prioritizes brevity over detail for performance.
    """
    
    LEVEL_CHARS = {
        "DEBUG": "D",
        "INFO": "I",
        "WARNING": "W",
        "ERROR": "E",
        "CRITICAL": "C"
    }
    
    def __init__(self, fmt: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        """Initialize the compact formatter."""
        super().__init__(fmt, *args, **kwargs)
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record in a compact format.
        
        Args:
            record: The log record to format
            
        Returns:
            Compact formatted string
        """
        # Format timestamp as HH:MM:SS.mmm
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        
        # Use short level indicator
        level = self.LEVEL_CHARS.get(record.levelname, "?")
        
        # Get short names
        module = record.module
        if len(module) > 12:
            module = module[:10] + ".."
        
        # Format the basic message
        msg = record.getMessage()
        
        # Add error type for error messages
        error_type = ""
        if record.levelno >= logging.ERROR:
            if hasattr(record, "error_context") and "error_type" in record.error_context:
                error_type = f"[{record.error_context['error_type']}] "
            elif record.exc_info:
                error_type = f"[{record.exc_info[0].__name__}] "
        
        # Build the compact message
        return f"{ts} {level} {module:12s} {error_type}{msg}"


def create_formatter(
    fmt_type: str = "json", use_colors: bool = True, fmt_string: Optional[str] = None
) -> logging.Formatter:
    """
    Create and return a formatter instance based on the specified type.

    Args:
        fmt_type (str): The type of formatter to create ("json", "text", or "compact").
        use_colors (bool): Whether to use colors in the text formatter.
        fmt_string (Optional[str]): An optional format string.

    Returns:
        logging.Formatter: The formatter instance.

    Raises:
        ValueError: If an unsupported formatter type is provided.
    """
    formatters = {
        "json": JSONFormatter,
        "text": TextFormatter,
        "compact": CompactFormatter,
    }
    fmt_type = fmt_type.lower()
    if fmt_type not in formatters:
        raise ValueError(f"Invalid formatter type: {fmt_type}. Must be one of: {', '.join(formatters.keys())}")

    formatter_class = formatters[fmt_type]
    if fmt_type == "text":
        return TextFormatter(fmt=fmt_string, use_colors=use_colors)
    return formatter_class(fmt=fmt_string)