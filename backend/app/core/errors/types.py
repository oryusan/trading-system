"""
Type definitions for the error handling system.

Features:
  - Error type definitions
  - Error context types
  - Recovery strategies
  - Error state tracking
  - Batch processing
"""

from datetime import datetime
from typing import Any, Dict, List, TypeVar, Union, Optional, Type, NamedTuple
from dataclasses import dataclass, field

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

@dataclass(frozen=True)
class RecoveryConfig:
    """Configuration for error recovery attempts."""
    max_retries: int = 3
    retry_delay: int = 1  # seconds
    max_delay: int = 30   # seconds
    backoff_factor: float = 2.0
    lock_timeout: int = 30  # seconds to wait for lock acquisition
    circuit_breaker_threshold: int = 5  # errors before circuit opens
    circuit_breaker_reset_time: int = 60  # seconds until circuit resets

# Global instance for recovery configuration
RECOVERY_CONFIG = RecoveryConfig()

@dataclass(frozen=True)
class NotificationConfig:
    """Configuration for error notifications."""
    notify_levels: List[ErrorLevel] = field(default_factory=lambda: [ErrorLevel.HIGH, ErrorLevel.CRITICAL])
    cooldown_period: int = 300  # seconds
    batch_size: int = 10
    telegram_enabled: bool = True

# Global instance for notification configuration
NOTIFICATION_CONFIG = NotificationConfig()

# Default recovery strategies mapping by error category.
DEFAULT_STRATEGIES: Dict[ErrorCategory, RecoveryStrategy] = {
    ErrorCategory.NETWORK: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.DATABASE: RecoveryStrategy.RETRY_WITH_BACKOFF,
    ErrorCategory.WEBSOCKET: RecoveryStrategy.RECONNECT,
    ErrorCategory.EXCHANGE: RecoveryStrategy.WAIT_AND_RETRY,
    ErrorCategory.RATELIMIT: RecoveryStrategy.WAIT_AND_RETRY,
    ErrorCategory.VALIDATION: RecoveryStrategy.RETRY,  # New default strategy
}

# Batch processing type for error handler
class BatchError(NamedTuple):
    """Container for batched errors."""
    error: Any  # BaseError, but we avoid importing to prevent circular imports
    context: Optional[Dict[str, Any]] = None
    error_class: Optional[Type] = None
    notification_override: Optional[bool] = None

# Circuit Breaker state tracking
@dataclass
class CircuitBreakerState:
    """Tracks state for the circuit breaker pattern."""
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    is_open: bool = False
    
    def register_failure(self) -> None:
        """Register a failure and update state."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()
    
    def register_success(self) -> None:
        """Register a success and update state."""
        self.failure_count = 0
        
    def should_attempt_operation(self, threshold: int, reset_time: int) -> bool:
        """Determine if an operation should be attempted based on circuit state."""
        # If circuit is closed, we're good to go
        if not self.is_open:
            return True
            
        # If circuit is open, check if enough time has passed to try again
        if self.last_failure_time is None:
            return True
            
        seconds_since_failure = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return seconds_since_failure >= reset_time

# Specific error context types
ExchangeErrorContext = Dict[str, Union[str, float, dict]]
WebSocketErrorContext = Dict[str, Union[str, int, bool]]
DatabaseErrorContext = Dict[str, Union[str, int, dict]]
ValidationErrorContext = Dict[str, Any]
NetworkErrorContext = Dict[str, Union[str, int, dict]]

# Type variables for generics in error handling
ErrorType = TypeVar("ErrorType", bound="BaseError")
HandlerType = TypeVar("HandlerType", bound="ErrorHandler")

# Error stack trace representation
class ErrorStackFrame(BaseModel):
    """Represents a single frame in an error stack trace."""
    filename: str
    lineno: int
    name: str
    line: Optional[str] = None

class ErrorStack(BaseModel):
    """Structured representation of an error stack trace."""
    frames: List[ErrorStackFrame]
    exception_type: str
    exception_value: str

class StructuredError(BaseModel):
    """Structured error representation for consistent serialization."""
    message: str
    error_type: str
    timestamp: datetime
    level: ErrorLevel
    category: ErrorCategory
    context: Dict[str, Any] = Field(default_factory=dict)
    stack: Optional[ErrorStack] = None
    
    class Config:
        arbitrary_types_allowed = True

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
    "NetworkErrorContext",
    "ErrorType",
    "HandlerType",
    "BatchError",
    "CircuitBreakerState",
    "ErrorStackFrame",
    "ErrorStack",
    "StructuredError",
]