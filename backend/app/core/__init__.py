"""
Core package initialization providing centralized access to commonly used functionality.
Optimized for lazy imports to avoid circular dependencies and improve startup time.
"""

from typing import Dict, Any, TYPE_CHECKING

# Direct imports that are safe and don't create circular dependencies
from .enums import (
    Environment, TimeFrame, PerformanceTimeFrame, ExchangeType, SignalOrderType, 
    OrderType, TradeSource, TradeStatus, PositionSide, BotStatus, PositionStatus, 
    WebSocketType, ConnectionState, UserRole, ErrorLevel, ErrorCategory, 
    RecoveryStrategy, LogLevel
)

# Import base errors directly as they're foundational
from .errors.base import (
    BaseError, ValidationError, AuthenticationError, AuthorizationError, 
    DatabaseError, NetworkError, ExchangeError, WebSocketError, 
    SystemError, NotFoundError, ServiceError, ConfigurationError
)

# Use lazy loading approach for more complex imports
# This allows circular references to be resolved at runtime

_lazy_modules: Dict[str, Any] = {}

def _lazy_import(module_name: str, names: list):
    """
    Lazily import a module and return requested attributes.
    This prevents circular imports by deferring import until needed.
    """
    def _import_module():
        if module_name not in _lazy_modules:
            # Actually import the module
            _lazy_modules[module_name] = __import__(
                module_name, globals(), locals(), names, 1
            )
        return _lazy_modules[module_name]
    
    # Return a lazy object that imports the module when accessed
    class _LazyModule:
        def __getattr__(self, name):
            if name not in names:
                raise AttributeError(f"{name} not found in {module_name}")
            return getattr(_import_module(), name)
    
    return _LazyModule()

# Lazy-load config components
if TYPE_CHECKING:
    from .config import settings, trading_constants, system_constants, DateString, Numeric
else:
    _config = _lazy_import("app.core.config", 
                         ["settings", "trading_constants", "system_constants", 
                          "DateString", "Numeric"])
    settings = _config.settings
    trading_constants = _config.trading_constants
    system_constants = _config.system_constants
    DateString = _config.DateString
    Numeric = _config.Numeric

# Lazy-load error handlers and types
if TYPE_CHECKING:
    from .errors.handlers import error_handler, handle_api_error
    from .errors.types import RecoveryConfig, NotificationConfig, ErrorContext, DEFAULT_STRATEGIES
else:
    _handlers = _lazy_import("app.core.errors.handlers", 
                           ["error_handler", "handle_api_error"])
    error_handler = _handlers.error_handler
    handle_api_error = _handlers.handle_api_error

    _types = _lazy_import("app.core.errors.types", 
                        ["RecoveryConfig", "NotificationConfig", "ErrorContext", 
                         "DEFAULT_STRATEGIES"])
    RecoveryConfig = _types.RecoveryConfig
    NotificationConfig = _types.NotificationConfig
    ErrorContext = _types.ErrorContext
    DEFAULT_STRATEGIES = _types.DEFAULT_STRATEGIES

# Lazy-load logging components
if TYPE_CHECKING:
    from .logging.logger import get_logger, init_logging, cleanup_logging
    from .logging.formatters import create_formatter
else:
    _logger = _lazy_import("app.core.logging.logger", 
                          ["get_logger", "init_logging", "cleanup_logging"])
    get_logger = _logger.get_logger
    init_logging = _logger.init_logging
    cleanup_logging = _logger.cleanup_logging

    _formatters = _lazy_import("app.core.logging.formatters", ["create_formatter"])
    create_formatter = _formatters.create_formatter

# Lazy-load references
if TYPE_CHECKING:
    from .references import (
        LoggerProtocol, DatabaseSessionProtocol, TokenProtocol, TradingServiceProtocol, 
        WebSocketManagerProtocol, ReferenceManagerProtocol, PerformanceServiceProtocol, 
        CacheProtocol, BaseTokenData, DateRange, BasePosition, BaseTrade, 
        BasePerformanceMetrics, PerformanceMetrics, LogRotation, RateLimits, 
        ExchangeTimeouts, WebhookConfig, CacheSettings, MonitoringSettings, 
        SettingsType, ConfigValidationError, ModelState, ValidationResult, 
        TimeRange, PerformanceDict, TimeSeriesData, validate_model_relationship, 
        validate_service_access, validate_role_assignment, MODEL_RELATIONSHIPS, 
        SERVICE_ACCESS, PageOptions, PagedResponse, AccessControl
    )
else:
    _refs = _lazy_import("app.core.references", 
                       ["LoggerProtocol", "DatabaseSessionProtocol", "TokenProtocol", 
                        "TradingServiceProtocol", "WebSocketManagerProtocol", 
                        "ReferenceManagerProtocol", "PerformanceServiceProtocol", 
                        "CacheProtocol", "BaseTokenData", "DateRange", "BasePosition", 
                        "BaseTrade", "BasePerformanceMetrics", "PerformanceMetrics", 
                        "LogRotation", "RateLimits", "ExchangeTimeouts", "WebhookConfig", 
                        "CacheSettings", "MonitoringSettings", "SettingsType", 
                        "ConfigValidationError", "ModelState", "ValidationResult", 
                        "TimeRange", "PerformanceDict", "TimeSeriesData", 
                        "validate_model_relationship", "validate_service_access", 
                        "validate_role_assignment", "MODEL_RELATIONSHIPS", "SERVICE_ACCESS",
                        "PageOptions", "PagedResponse", "AccessControl"])
    
    # Export all reference components
    LoggerProtocol = _refs.LoggerProtocol
    DatabaseSessionProtocol = _refs.DatabaseSessionProtocol
    TokenProtocol = _refs.TokenProtocol
    TradingServiceProtocol = _refs.TradingServiceProtocol
    WebSocketManagerProtocol = _refs.WebSocketManagerProtocol
    ReferenceManagerProtocol = _refs.ReferenceManagerProtocol
    PerformanceServiceProtocol = _refs.PerformanceServiceProtocol
    CacheProtocol = _refs.CacheProtocol
    BaseTokenData = _refs.BaseTokenData
    DateRange = _refs.DateRange
    BasePosition = _refs.BasePosition
    BaseTrade = _refs.BaseTrade
    BasePerformanceMetrics = _refs.BasePerformanceMetrics
    PerformanceMetrics = _refs.PerformanceMetrics
    LogRotation = _refs.LogRotation
    RateLimits = _refs.RateLimits
    ExchangeTimeouts = _refs.ExchangeTimeouts
    WebhookConfig = _refs.WebhookConfig
    CacheSettings = _refs.CacheSettings
    MonitoringSettings = _refs.MonitoringSettings
    SettingsType = _refs.SettingsType
    ConfigValidationError = _refs.ConfigValidationError
    ModelState = _refs.ModelState
    ValidationResult = _refs.ValidationResult
    TimeRange = _refs.TimeRange
    PerformanceDict = _refs.PerformanceDict
    TimeSeriesData = _refs.TimeSeriesData
    validate_model_relationship = _refs.validate_model_relationship
    validate_service_access = _refs.validate_service_access
    validate_role_assignment = _refs.validate_role_assignment
    MODEL_RELATIONSHIPS = _refs.MODEL_RELATIONSHIPS
    SERVICE_ACCESS = _refs.SERVICE_ACCESS
    PageOptions = _refs.PageOptions
    PagedResponse = _refs.PagedResponse
    AccessControl = _refs.AccessControl

__all__ = [
    # Config
    "settings", "trading_constants", "system_constants", "DateString", "Numeric",
    # Base Errors
    "BaseError", "ValidationError", "AuthenticationError", "AuthorizationError", 
    "DatabaseError", "NetworkError", "ExchangeError", "WebSocketError", "SystemError", 
    "NotFoundError", "ServiceError", "ConfigurationError",
    # Error handlers and types
    "error_handler", "handle_api_error", "RecoveryConfig", "NotificationConfig", 
    "ErrorContext", "DEFAULT_STRATEGIES", 
    # Logging
    "get_logger", "init_logging", "cleanup_logging", "create_formatter",
    # Enums
    "Environment", "TimeFrame", "PerformanceTimeFrame", "ExchangeType", 
    "SignalOrderType", "OrderType", "TradeSource", "TradeStatus", "PositionSide", "BotStatus", 
    "PositionStatus", "WebSocketType", "ConnectionState", "UserRole", "ErrorLevel", 
    "ErrorCategory", "RecoveryStrategy", "LogLevel", 
    # Protocols, Models, and References
    "LoggerProtocol", "DatabaseSessionProtocol", "TokenProtocol", "TradingServiceProtocol", 
    "WebSocketManagerProtocol", "ReferenceManagerProtocol", "PerformanceServiceProtocol", 
    "CacheProtocol", "BaseTokenData", "DateRange", "BasePosition", "BaseTrade", 
    "BasePerformanceMetrics", "PerformanceMetrics", "LogRotation", "RateLimits", 
    "ExchangeTimeouts", "WebhookConfig", "CacheSettings", "MonitoringSettings", 
    "SettingsType", "ConfigValidationError", "ModelState", "ValidationResult", 
    "TimeRange", "PerformanceDict", "TimeSeriesData", "validate_model_relationship", 
    "validate_service_access", "validate_role_assignment", "MODEL_RELATIONSHIPS", 
    "SERVICE_ACCESS", "PageOptions", "PagedResponse", "AccessControl"
]