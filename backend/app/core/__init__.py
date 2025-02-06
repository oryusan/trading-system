"""Core package initialization providing centralized access to commonly used functionality."""

# Configuration
from app.core.config.settings import settings
from app.core.config.constants import (
    trading_constants,
    system_constants,
    DateString,
    Numeric
)

# Error System
from app.core.errors.base import (
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
    RecoveryTimeouts,
    ErrorContext,
    DEFAULT_STRATEGIES
)

# Logging System
from app.core.logging.logger import (
    get_logger,
    init_logging,
    cleanup_logging
)
from app.core.logging.formatters import (
    LogLevel,
    ErrorLevel,
    create_formatter
)

# References
from app.core.references import (
    # Enums
    Environment,
    TimeFrame,
    PerformanceTimeFrame,
    ExchangeType,
    SignalOrderType,
    OrderType,
    TradeSource,
    TradeStatus,
    PositionSide,
    BotStatus,
    PositionStatus,
    WebSocketType,
    ConnectionState,
    UserRole,
    
    # Protocols
    LoggerProtocol,
    DatabaseSessionProtocol,
    TokenProtocol,
    TradingServiceProtocol,
    WebSocketManagerProtocol,
    ReferenceManagerProtocol,
    PerformanceServiceProtocol,
    
    # Base Models
    BaseTokenData,
    DateRange,
    BasePosition,
    BaseTrade, 
    BasePerformanceMetrics,
    PerformanceMetrics,
    
    # Configuration Models
    LogRotation,
    RateLimits,
    ExchangeTimeouts,
    WebhookConfig,
    CacheSettings,
    MonitoringSettings,
    SettingsType,
    ConfigValidationError,
    
    # Type Definitions
    DateString,
    TimestampString,
    Numeric,
    ModelState,
    ValidationResult,
    TimeRange,
    PerformanceDict,
    TimeSeriesData,
    MetricsResponse,
    WebSocketMessage,
    WebSocketStatus,
    SubscriptionRequest,
    
    # Utility Functions
    validate_model_relationship,
    validate_service_access,
    validate_role_assignment,
    
    # Relationships
    MODEL_RELATIONSHIPS,
    SERVICE_ACCESS
)

__all__ = [
    # Configuration
    "settings",
    "trading_constants",
    "system_constants",
    "DateString",
    "Numeric",
    
    # Error System
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "DatabaseError",
    "NetworkError",
    "ExchangeError",
    "WebSocketError",
    "SystemError",
    "ErrorLevel",
    "ErrorCategory",
    "error_handler",
    "handle_api_error",
    "RecoveryStrategy",
    "RecoveryConfig",
    "NotificationConfig",
    "RecoveryTimeouts",
    "ErrorContext",
    "DEFAULT_STRATEGIES",
    
    # Logging System
    "get_logger",
    "init_logging",
    "cleanup_logging",
    "LogLevel",
    "ErrorLevel",
    "create_formatter",
    
    # Enums
    "Environment",
    "TimeFrame",
    "PerformanceTimeFrame",
    "ExchangeType",
    "SignalOrderType",
    "OrderType",
    "TradeSource",
    "TradeStatus",
    "PositionSide",
    "BotStatus",
    "PositionStatus",
    "WebSocketType",
    "ConnectionState",
    "UserRole",
    
    # Protocols
    "LoggerProtocol",
    "DatabaseSessionProtocol",
    "TokenProtocol",
    "TradingServiceProtocol",
    "WebSocketManagerProtocol",
    "ReferenceManagerProtocol",
    "PerformanceServiceProtocol",
    
    # Base Models
    "BaseTokenData",
    "DateRange",
    "BasePosition",
    "BaseTrade",
    "BasePerformanceMetrics",
    "PerformanceMetrics",
    
    # Configuration Models
    "LogRotation",
    "RateLimits", 
    "ExchangeTimeouts",
    "WebhookConfig",
    "CacheSettings",
    "MonitoringSettings",
    "SettingsType",
    "ConfigValidationError",
    
    # Type Definitions
    "DateString",
    "TimestampString",
    "Numeric",
    "ModelState",
    "ValidationResult", 
    "TimeRange",
    "PerformanceDict",
    "TimeSeriesData",
    "MetricsResponse",
    "WebSocketMessage",
    "WebSocketStatus",
    "SubscriptionRequest",
    
    # Utility Functions
    "validate_model_relationship",
    "validate_service_access",
    "validate_role_assignment",
    
    # Relationships
    "MODEL_RELATIONSHIPS",
    "SERVICE_ACCESS"
]