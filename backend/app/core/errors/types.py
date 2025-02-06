"""
Type definitions for error handling system.

Features:
- Error type definitions
- Error context types
- Recovery strategies
- Error state tracking
"""

from typing import Dict, Any, Optional, Union, List, TypeVar
from datetime import datetime
from enum import Enum

# Error Types
ErrorCode = str
ErrorMessage = str
ErrorTrace = str
ErrorContext = Dict[str, Any]

# Error States
ErrorState = Dict[str, Union[bool, int, datetime]]
ErrorMetrics = Dict[str, int]

class ErrorLevel(str, Enum):
    """Error severity levels for notification and handling."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(str, Enum):
    """Error categories for classification and routing."""
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATABASE = "database"
    EXCHANGE = "exchange"
    NETWORK = "network"
    WEBSOCKET = "websocket"
    SYSTEM = "system"

class RecoveryStrategy(str, Enum):
    """Available error recovery strategies."""
    RETRY = "retry"
    WAIT_AND_RETRY = "wait_and_retry"
    RECONNECT = "reconnect"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    CANCEL_AND_RETRY = "cancel_and_retry"
    CLOSE_AND_RESET = "close_and_reset"

# Error Recovery Configuration
class RecoveryConfig:
    """Configuration for error recovery attempts."""
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1  # seconds
    MAX_DELAY: int = 30   # seconds
    BACKOFF_FACTOR: float = 2.0

# Error Notification Configuration
class NotificationConfig:
    """Configuration for error notifications."""
    NOTIFY_LEVELS: List[ErrorLevel] = [
        ErrorLevel.HIGH,
        ErrorLevel.CRITICAL
    ]
    COOLDOWN_PERIOD: int = 300  # seconds
    BATCH_SIZE: int = 10

# Default Recovery Strategies
DEFAULT_STRATEGIES: Dict[ErrorCategory, RecoveryStrategy] = {
    ErrorCategory.NETWORK: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.DATABASE: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.WEBSOCKET: RecoveryStrategy.RECONNECT,
    ErrorCategory.EXCHANGE: RecoveryStrategy.WAIT_AND_RETRY
}

# Error Context Types
ExchangeErrorContext = Dict[str, Union[str, float, dict]]
WebSocketErrorContext = Dict[str, Union[str, int, bool]]
DatabaseErrorContext = Dict[str, Union[str, int, dict]]
ValidationErrorContext = Dict[str, Any]

# Type Variables
ErrorType = TypeVar("ErrorType", bound="BaseError")
HandlerType = TypeVar("HandlerType", bound="ErrorHandler")