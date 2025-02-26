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
import os
import socket
from datetime import datetime
from typing import Any, Optional, Dict, List
from enum import Enum

# Define the set of standard LogRecord attributes so we can merge extra fields.
STANDARD_LOG_ATTRS = {
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
        error = getattr(record, "error_context", None)
        if error:
            return {
                "type": error.get("error_type"),
                "message": error.get("message"),
                "level": error.get("level"),
                "category": error.get("category"),
                "context": error.get("context"),
                "traceback": error.get("traceback"),
            }
        return None

    def get_exception_info(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            return {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            }
        return None

    def get_request_context(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        return getattr(record, "request_context", None)

    def get_performance_info(self, record: logging.LogRecord) -> Optional[Dict[str, Any]]:
        return getattr(record, "performance", None)

    def get_extra_fields(
        self, record: logging.LogRecord, base: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if base is None:
            base = {}
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_LOG_ATTRS and key not in base
        }


class JSONFormatter(BaseLogFormatter):
    """
    JSON formatter that outputs log records as JSON strings.
    It adds extra context such as hostname and process ID.
    """

    def __init__(self, fmt: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(fmt, *args, **kwargs)
        self.hostname = self._get_hostname()
        self.pid = self._get_process_id()

    def _get_hostname(self) -> str:
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _get_process_id(self) -> int:
        try:
            return os.getpid()
        except Exception:
            return -1

    def _base_log_data(self, record: logging.LogRecord) -> Dict[str, Any]:
        return {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": {"id": self.pid, "name": record.processName},
            "host": self.hostname,
        }

    def _json_serializer(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        try:
            return str(obj)
        except Exception:
            return None

    def format(self, record: logging.LogRecord) -> str:
        log_data = self._base_log_data(record)
        if error_ctx := self.get_error_context(record):
            log_data["error"] = error_ctx
        if exc := self.get_exception_info(record):
            log_data["exception"] = exc
        if req_ctx := self.get_request_context(record):
            log_data["request"] = req_ctx
        if perf := self.get_performance_info(record):
            log_data["performance"] = perf
        log_data.update(self.get_extra_fields(record, log_data))
        return json.dumps(log_data, default=self._json_serializer)


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
        super().__init__(fmt, *args, **kwargs)
        self.use_colors = use_colors

    def _format_error_context(self, record: logging.LogRecord) -> List[str]:
        lines = []
        error = self.get_error_context(record)
        if error:
            lines.append(f"Error: {error.get('type')} ({error.get('level', 'UNKNOWN')})")
            lines.append(f"Message: {error.get('message')}")
            lines.append(f"Context: {json.dumps(error.get('context', {}))}")
            if tb := error.get("traceback"):
                lines.append(f"Traceback:\n{tb}")
        return lines

    def _format_exception(self, record: logging.LogRecord) -> List[str]:
        lines = []
        exc = self.get_exception_info(record)
        if exc:
            lines.append(f"Exception: {exc.get('type')}")
            lines.append(f"Message: {exc.get('message')}")
            lines.append("Traceback:")
            lines.append(exc.get("traceback", ""))
        return lines

    def _format_request_context(self, record: logging.LogRecord) -> List[str]:
        lines = []
        req = self.get_request_context(record)
        if req:
            lines.append(
                f"Request: {req.get('method', 'UNKNOWN')} {req.get('path', 'UNKNOWN')} ({req.get('id', 'UNKNOWN')})"
            )
        return lines

    def _format_performance(self, record: logging.LogRecord) -> List[str]:
        lines = []
        performance = self.get_performance_info(record)
        if performance:
            metrics = ", ".join(
                f"{k}: {v}ms" for k, v in performance.items() if isinstance(v, (int, float))
            )
            if metrics:
                lines.append("Performance: " + metrics)
        return lines

    def _format_extra_fields(self, record: logging.LogRecord) -> List[str]:
        extras = self.get_extra_fields(record)
        return [f"{key}: {value}" for key, value in extras.items()]

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "") if self.use_colors else ""
        reset = self.COLORS["RESET"] if self.use_colors else ""
        header = f"{self.formatTime(record)} {color}{record.levelname}{reset} [{record.name}] {record.getMessage()}"
        parts = [header]
        parts.extend(self._format_error_context(record))
        parts.extend(self._format_exception(record))
        parts.extend(self._format_request_context(record))
        parts.extend(self._format_performance(record))
        parts.extend(self._format_extra_fields(record))
        return "\n".join(parts)


def create_formatter(
    fmt_type: str = "json", use_colors: bool = True, fmt_string: Optional[str] = None
) -> logging.Formatter:
    """
    Create and return a formatter instance based on the specified type.

    Args:
        fmt_type (str): The type of formatter to create ("json" or "text").
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
    }
    fmt_type = fmt_type.lower()
    if fmt_type not in formatters:
        raise ValueError(f"Invalid formatter type: {fmt_type}")

    if fmt_type == "text":
        return TextFormatter(fmt=fmt_string, use_colors=use_colors)
    return formatters[fmt_type](fmt=fmt_string)
