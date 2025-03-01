import traceback
from datetime import datetime
from typing import Any, Dict, Optional, Type

from app.core.enums import ErrorLevel, ErrorCategory


class BaseError(Exception):
    """
    Base error class providing rich context and serialization.
    """
    default_level: ErrorLevel = ErrorLevel.MEDIUM
    default_category: ErrorCategory = ErrorCategory.SYSTEM

    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        parent: Optional[Exception] = None,
        level: Optional[ErrorLevel] = None,
        category: Optional[ErrorCategory] = None,
        *args: Any
    ) -> None:
        super().__init__(message, *args)
        self.message = message
        self.context = context or {}
        self.parent = parent
        self.level = level or self.default_level
        self.category = category or self.default_category
        self.timestamp = datetime.utcnow()
        self.traceback = (
            "".join(traceback.format_exception(type(parent), parent, parent.__traceback__))
            if parent
            else None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the error instance into a dictionary."""
        return {
            "message": self.message,
            "context": self.context,
            "level": self.level.value if hasattr(self.level, "value") else self.level,
            "category": self.category.value if hasattr(self.category, "value") else self.category,
            "timestamp": self.timestamp.isoformat(),
            "parent_error": str(self.parent) if self.parent else None,
            "traceback": self.traceback,
            "error_type": self.__class__.__name__,
        }

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        context: Optional[Dict[str, Any]] = None,
        level: Optional[ErrorLevel] = None,
        category: Optional[ErrorCategory] = None,
    ) -> "BaseError":
        """Create an error instance from an existing exception."""
        return cls(
            message=str(exc),
            context=context,
            parent=exc,
            level=level,
            category=category,
        )

    def add_context(self, **kwargs: Any) -> "BaseError":
        """Add additional context information to the error."""
        self.context.update(kwargs)
        return self

    def with_traceback(self, tb: Any) -> "BaseError":
        """Attach a traceback to the error."""
        self.traceback = "".join(traceback.format_tb(tb))
        return super().with_traceback(tb)


class ValidationError(BaseError):
    default_level = ErrorLevel.MEDIUM
    default_category = ErrorCategory.VALIDATION


class AuthenticationError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.AUTHENTICATION


class AuthorizationError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.AUTHORIZATION


class DatabaseError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.DATABASE


class NetworkError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.NETWORK


class NotFoundError(BaseError):
    default_level = ErrorLevel.MEDIUM
    default_category = ErrorCategory.SYSTEM


class ExchangeError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.EXCHANGE


class WebSocketError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.WEBSOCKET


class RateLimitError(BaseError):
    default_level = ErrorLevel.MEDIUM
    default_category = ErrorCategory.RATELIMIT


class SystemError(BaseError):
    default_level = ErrorLevel.CRITICAL
    default_category = ErrorCategory.SYSTEM


class ServiceError(BaseError):
    default_level = ErrorLevel.HIGH
    default_category = ErrorCategory.SYSTEM


class ConfigurationError(BaseError):
    """
    Configuration error raised when settings are invalid.
    """
    default_level = ErrorLevel.CRITICAL
    default_category = ErrorCategory.SYSTEM

class RequestException(Exception):
    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        self.message = message
        self.context = context or {}
        super().__init__(f"{message} | Context: {self.context}")

def get_error_class(category: ErrorCategory) -> Type[BaseError]:
    """
    Return the appropriate error class for a given error category.
    
    Args:
        category (ErrorCategory): The category of the error.
    
    Returns:
        Type[BaseError]: The error class corresponding to the category.
    """
    error_map = {
        ErrorCategory.VALIDATION: ValidationError,
        ErrorCategory.AUTHENTICATION: AuthenticationError,
        ErrorCategory.AUTHORIZATION: AuthorizationError,
        ErrorCategory.DATABASE: DatabaseError,
        ErrorCategory.EXCHANGE: ExchangeError,
        ErrorCategory.NETWORK: NetworkError,
        ErrorCategory.WEBSOCKET: WebSocketError,
        ErrorCategory.RATELIMIT: RateLimitError,
        ErrorCategory.SYSTEM: SystemError,
    }
    return error_map.get(category, BaseError)
