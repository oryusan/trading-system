"""
Type definitions for the error handling system.

Features:
  - Error type definitions
  - Error context types
  - Recovery strategies
  - Error state tracking
"""

from datetime import datetime
from typing import Any, Dict, List, TypeVar, Union

from pydantic import BaseModel, Field

from app.core.enums import ErrorLevel, ErrorCategory, RecoveryStrategy

# Basic type aliases
ErrorCode = str
ErrorMessage = str
ErrorTrace = str
ErrorContext = Dict[str, Any]

# Error state and metrics types
ErrorState = Dict[str, Union[bool, int, datetime]]
ErrorMetrics = Dict[str, int]

from dataclasses import dataclass, field

@dataclass(frozen=True)
class RecoveryConfig:
    """Configuration for error recovery attempts."""
    max_retries: int = 3
    retry_delay: int = 1  # seconds
    max_delay: int = 30   # seconds
    backoff_factor: float = 2.0

# Global instance for recovery configuration
RECOVERY_CONFIG = RecoveryConfig()

@dataclass(frozen=True)
class NotificationConfig:
    """Configuration for error notifications."""
    notify_levels: List[ErrorLevel] = field(default_factory=lambda: [ErrorLevel.HIGH, ErrorLevel.CRITICAL])
    cooldown_period: int = 300  # seconds
    batch_size: int = 10

# Global instance for notification configuration
NOTIFICATION_CONFIG = NotificationConfig()

# Default recovery strategies mapping by error category.
DEFAULT_STRATEGIES: Dict[ErrorCategory, RecoveryStrategy] = {
    ErrorCategory.NETWORK: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.DATABASE: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.WEBSOCKET: RecoveryStrategy.RECONNECT,
    ErrorCategory.EXCHANGE: RecoveryStrategy.WAIT_AND_RETRY,
}

# Specific error context types.
ExchangeErrorContext = Dict[str, Union[str, float, dict]]
WebSocketErrorContext = Dict[str, Union[str, int, bool]]
DatabaseErrorContext = Dict[str, Union[str, int, dict]]
ValidationErrorContext = Dict[str, Any]

# Type variables for generics in error handling.
ErrorType = TypeVar("ErrorType", bound="BaseError")
HandlerType = TypeVar("HandlerType", bound="ErrorHandler")

__all__ = [
    "ErrorCode",
    "ErrorMessage",
    "ErrorTrace",
    "ErrorContext",
    "ErrorState",
    "ErrorMetrics",
    "RecoveryConfig",
    "NotificationConfig",
    "RECOVERY_CONFIG",
    "NOTIFICATION_CONFIG",
    "DEFAULT_STRATEGIES",
    "ExchangeErrorContext",
    "WebSocketErrorContext",
    "DatabaseErrorContext",
    "ValidationErrorContext",
    "ErrorType",
    "HandlerType",
]
