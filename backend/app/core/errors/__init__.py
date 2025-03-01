"""
Core error handling system providing centralized error management.

Exports:
- Base error classes and custom errors
- Error handling utilities and type definitions
"""

# Import base errors directly to avoid circular dependencies
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
    ConfigurationError,
    RequestException,
    get_error_class
)

# Use lazy loading for handlers and types to prevent circular imports
import importlib
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

# Type-checking imports
if TYPE_CHECKING:
    from .handlers import error_handler, handle_api_error
    from .types import (
        RecoveryConfig,
        NotificationConfig,
        ErrorContext,
        DEFAULT_STRATEGIES,
        BatchError,
        CircuitBreakerState,
        ErrorStackFrame,
        ErrorStack,
        StructuredError,
    )
    from ..enums import (
        ErrorLevel,
        ErrorCategory,
        RecoveryStrategy,
    )
else:
    # Create lazy-loaded handler module
    _handlers_module = None
    
    def _get_handlers_module():
        global _handlers_module
        if _handlers_module is None:
            _handlers_module = importlib.import_module('.handlers', package='app.core.errors')
        return _handlers_module
    
    # Create lazy-loaded types module  
    _types_module = None
    
    def _get_types_module():
        global _types_module
        if _types_module is None:
            _types_module = importlib.import_module('.types', package='app.core.errors')
        return _types_module
    
    # Create lazy accessors for handler objects
    @property
    def error_handler():
        return _get_handlers_module().error_handler
    
    @property
    def handle_api_error():
        return _get_handlers_module().handle_api_error
    
    # Create lazy accessors for type objects
    @property
    def RecoveryConfig():
        return _get_types_module().RecoveryConfig
    
    @property
    def NotificationConfig():
        return _get_types_module().NotificationConfig
    
    @property
    def ErrorContext():
        return _get_types_module().ErrorContext
    
    @property
    def DEFAULT_STRATEGIES():
        return _get_types_module().DEFAULT_STRATEGIES
    
    @property
    def BatchError():
        return _get_types_module().BatchError
    
    @property
    def CircuitBreakerState():
        return _get_types_module().CircuitBreakerState
    
    @property
    def ErrorStackFrame():
        return _get_types_module().ErrorStackFrame
    
    @property
    def ErrorStack():
        return _get_types_module().ErrorStack
    
    @property
    def StructuredError():
        return _get_types_module().StructuredError

# Import enums
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
    "RequestException",
    "get_error_class",
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
    "BatchError",
    "CircuitBreakerState",
    "ErrorStackFrame",
    "ErrorStack",
    "StructuredError",
]