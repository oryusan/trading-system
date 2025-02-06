"""
Base error classes for the application with improved context handling.

This module provides the foundation for error handling across the application:
- Hierarchical error structure
- Rich error context
- Error serialization
- Error classification
"""

from typing import Dict, Any, Optional, Type, List
from datetime import datetime
from enum import Enum
import traceback

class ErrorLevel(str, Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(str, Enum):
    """Error category classification."""
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATABASE = "database"
    EXCHANGE = "exchange"
    NETWORK = "network"
    WEBSOCKET = "websocket"
    SYSTEM = "system"

class BaseError(Exception):
    """
    Base error class providing rich context and serialization.
    
    Features:
    - Error context capture
    - Stack trace handling
    - Error categorization
    - Timestamp tracking
    """
    
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        level: ErrorLevel = ErrorLevel.MEDIUM,
        category: ErrorCategory = ErrorCategory.SYSTEM,
        *args: Any
    ) -> None:
        super().__init__(message, *args)
        self.message = message
        self.context = context or {}
        self.parent = parent
        self.level = level
        self.category = category
        self.timestamp = datetime.utcnow()
        self.traceback = None
        
        if parent:
            self.traceback = "".join(traceback.format_exception(
                type(parent),
                parent,
                parent.__traceback__
            ))

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary format."""
        return {
            "message": self.message,
            "context": self.context,
            "level": self.level,
            "category": self.category,
            "timestamp": self.timestamp.isoformat(),
            "parent_error": str(self.parent) if self.parent else None,
            "traceback": self.traceback,
            "error_type": self.__class__.__name__
        }

    @classmethod
    def from_exception(
        cls, 
        exc: Exception,
        context: Optional[Dict[str, Any]] = None,
        level: Optional[ErrorLevel] = None,
        category: Optional[ErrorCategory] = None
    ) -> "BaseError":
        """Create error instance from another exception."""
        return cls(
            message=str(exc),
            context=context,
            parent=exc,
            level=level or ErrorLevel.MEDIUM,
            category=category or ErrorCategory.SYSTEM
        )

    def add_context(self, **kwargs: Any) -> "BaseError":
        """Add additional context to error."""
        self.context.update(kwargs)
        return self

    def with_traceback(self, tb: Any) -> "BaseError":
        """Add traceback information."""
        self.traceback = "".join(traceback.format_tb(tb))
        return super().with_traceback(tb)

class ValidationError(BaseError):
    """Validation related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.MEDIUM,
            category=ErrorCategory.VALIDATION,
            **kwargs
        )

class AuthenticationError(BaseError):
    """Authentication related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.AUTHENTICATION,
            **kwargs
        )

class AuthorizationError(BaseError):
    """Authorization related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.AUTHORIZATION,
            **kwargs
        )

class DatabaseError(BaseError):
    """Database related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.DATABASE,
            **kwargs
        )

class NetworkError(BaseError):
    """Network related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.NETWORK,
            **kwargs
        )

class ExchangeError(BaseError):
    """Exchange related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.EXCHANGE,
            **kwargs
        )

class WebSocketError(BaseError):
    """WebSocket related errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.HIGH,
            category=ErrorCategory.WEBSOCKET,
            **kwargs
        )

class SystemError(BaseError):
    """System level errors."""
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        **kwargs: Any
    ) -> None:
        super().__init__(
            message=message,
            context=context,
            parent=parent,
            level=ErrorLevel.CRITICAL,
            category=ErrorCategory.SYSTEM,
            **kwargs
        )

def get_error_class(category: ErrorCategory) -> Type[BaseError]:
    """Get appropriate error class for a category."""
    error_map = {
        ErrorCategory.VALIDATION: ValidationError,
        ErrorCategory.AUTHENTICATION: AuthenticationError,
        ErrorCategory.AUTHORIZATION: AuthorizationError,
        ErrorCategory.DATABASE: DatabaseError,
        ErrorCategory.EXCHANGE: ExchangeError,
        ErrorCategory.NETWORK: NetworkError,
        ErrorCategory.WEBSOCKET: WebSocketError,
        ErrorCategory.SYSTEM: SystemError
    }
    return error_map.get(category, BaseError)