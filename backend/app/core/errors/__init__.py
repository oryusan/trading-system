"""
Core error handling system providing centralized error management.

Exports:
- Base error classes and custom errors
- Error handling utilities and type definitions
"""

from .base import (
    BaseError,
    ValidationError,
    AuthenticationError,
    AuthorizationError,
    DatabaseError,
    NetworkError,
    ExchangeError,
    WebSocketError,
    SystemError,
    NotFoundError,
    ServiceError,
    ConfigurationError
)

from .handlers import (
    error_handler,
    handle_api_error,
)

from .types import (
    RecoveryConfig,
    NotificationConfig,
    ErrorContext,
    DEFAULT_STRATEGIES,
)

from ..enums import (
    ErrorLevel,
    ErrorCategory,
    RecoveryStrategy,
)

__all__ = [
    # Base Errors
    "BaseError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "DatabaseError",
    "NetworkError",
    "ExchangeError",
    "WebSocketError",
    "SystemError",
    "NotFoundError",
    "ServiceError",
    "ConfigurationError",
    # Enums
    "ErrorLevel",
    "ErrorCategory",
    "RecoveryStrategy",
    # Handlers
    "error_handler",
    "handle_api_error",
    # Types
    "RecoveryConfig",
    "NotificationConfig",
    "ErrorContext",
    "DEFAULT_STRATEGIES",
]
