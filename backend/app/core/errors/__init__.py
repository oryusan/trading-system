"""
Core error handling system providing centralized error management.

Exports:
- Base error classes
- Error handling utilities
- Error type definitions
"""

from app.core.errors.base import (
    BaseError,
    ValidationError,
    AuthenticationError,
    AuthorizationError,
    DatabaseError,
    NetworkError,
    ExchangeError,
    WebSocketError,
    SystemError,
    ErrorLevel,
    ErrorCategory
)

from app.core.errors.handlers import (
    error_handler,
    handle_api_error
)

from app.core.errors.types import (
    RecoveryStrategy,
    RecoveryConfig,
    NotificationConfig,
    ErrorContext,
    DEFAULT_STRATEGIES
)

__all__ = [
    # Base Errors
    'BaseError',
    'ValidationError',
    'AuthenticationError',
    'AuthorizationError',
    'DatabaseError',
    'NetworkError',
    'ExchangeError',
    'WebSocketError',
    'SystemError',
    
    # Enums
    'ErrorLevel',
    'ErrorCategory',
    
    # Handlers
    'error_handler',
    'handle_api_error',
    
    # Types
    'RecoveryStrategy',
    'RecoveryConfig',
    'NotificationConfig',
    'ErrorContext',
    'DEFAULT_STRATEGIES'
]