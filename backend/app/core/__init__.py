"""
Core package initialization providing centralized access to commonly used functionality.
"""

from .config import settings, trading_constants, system_constants, DateString, Numeric
from .errors.base import (
    BaseError, ValidationError, AuthenticationError, AuthorizationError, 
    DatabaseError, NetworkError, ExchangeError, WebSocketError, 
    SystemError, NotFoundError, ServiceError, ConfigurationError
)
from .errors.handlers import error_handler, handle_api_error
from .errors.types import RecoveryConfig, NotificationConfig, ErrorContext, DEFAULT_STRATEGIES
from .logging.logger import get_logger, init_logging, cleanup_logging
from .logging.formatters import create_formatter
from .enums import (
    Environment, TimeFrame, PerformanceTimeFrame, ExchangeType, SignalOrderType, 
    OrderType, TradeSource, TradeStatus, PositionSide, BotStatus, PositionStatus, 
    WebSocketType, ConnectionState, UserRole, ErrorLevel, ErrorCategory, 
    RecoveryStrategy, LogLevel
)
from .references import (
    LoggerProtocol, DatabaseSessionProtocol, TokenProtocol, TradingServiceProtocol, 
    WebSocketManagerProtocol, ReferenceManagerProtocol, PerformanceServiceProtocol, 
    BaseTokenData, DateRange, BasePosition, BaseTrade, BasePerformanceMetrics, 
    PerformanceMetrics, LogRotation, RateLimits, ExchangeTimeouts, WebhookConfig, 
    CacheSettings, MonitoringSettings, SettingsType, ConfigValidationError, 
    ModelState, ValidationResult, TimeRange, PerformanceDict, TimeSeriesData, 
    validate_model_relationship, validate_service_access, validate_role_assignment, 
    MODEL_RELATIONSHIPS, SERVICE_ACCESS
)

__all__ = [
    "settings", "trading_constants", "system_constants", "DateString", "Numeric",
    "BaseError", "ValidationError", "AuthenticationError", "AuthorizationError", 
    "DatabaseError", "NetworkError", "ExchangeError", "WebSocketError", "SystemError", 
    "NotFoundError", "ServiceError", "ConfigurationError",
    "error_handler", "handle_api_error", "RecoveryConfig", "NotificationConfig", 
    "ErrorContext", "DEFAULT_STRATEGIES", "get_logger", "init_logging", "cleanup_logging", 
    "create_formatter", "Environment", "TimeFrame", "PerformanceTimeFrame", "ExchangeType", 
    "SignalOrderType", "OrderType", "TradeSource", "TradeStatus", "PositionSide", "BotStatus", 
    "PositionStatus", "WebSocketType", "ConnectionState", "UserRole", "ErrorLevel", 
    "ErrorCategory", "RecoveryStrategy", "LogLevel", "LoggerProtocol", "DatabaseSessionProtocol", 
    "TokenProtocol", "TradingServiceProtocol", "WebSocketManagerProtocol", "ReferenceManagerProtocol", 
    "PerformanceServiceProtocol", "BaseTokenData", "DateRange", "BasePosition", "BaseTrade", 
    "BasePerformanceMetrics", "PerformanceMetrics", "LogRotation", "RateLimits", "ExchangeTimeouts", 
    "WebhookConfig", "CacheSettings", "MonitoringSettings", "SettingsType", "ConfigValidationError", 
    "ModelState", "ValidationResult", "TimeRange", "PerformanceDict", "TimeSeriesData", 
    "validate_model_relationship", "validate_service_access", "validate_role_assignment", 
    "MODEL_RELATIONSHIPS", "SERVICE_ACCESS"
]
