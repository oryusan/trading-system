"""
Refactored settings module for FastAPI using Pydantic v2.
Optimized for performance, security, and maintainability.
"""

import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Set, Dict, Union, Any

from pydantic import (
    BaseModel,
    Field,
    model_validator,
    validator,
    SecretStr,
    constr,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Imports from your project
from app.core.errors.types import NotificationConfig
from app.core.enums import LogLevel, ErrorLevel, RecoveryStrategy, Environment
from app.core.references import DatabaseConfig, ConfigValidationError

# ─────────────────────────────────────────────────────────────────────────────
# Custom Constrained Types for URLs
# ─────────────────────────────────────────────────────────────────────────────

MongoDBUrl = constr(pattern=r"^mongodb(\+srv)?://.*", strip_whitespace=True)
RedisUrl = constr(pattern=r"^redis://.*", strip_whitespace=True)

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions for Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_secret(value: Union[str, SecretStr], min_length: int = 20) -> SecretStr:
    """Validate secret value meets minimum length requirements."""
    token = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not token or len(token) < min_length:
        raise ValueError(f"Secret must be at least {min_length} characters long.")
    return SecretStr(token)

# ─────────────────────────────────────────────────────────────────────────────
# Nested Models for Different Configuration Areas
# ─────────────────────────────────────────────────────────────────────────────

class AppSettings(BaseModel):
    """Application-level settings."""
    PROJECT_NAME: str = Field(
        default="Trading WebApp",
        description="Name of the project",
        min_length=1,
        max_length=100,
    )
    VERSION: str = Field(
        default="1.0.0",
        description="API version",
        pattern=r"^\d+\.\d+\.\d+$",
    )
    API_V1_STR: str = Field(
        default="/api/v1",
        env="APP_API_V1_STR",
        description="API v1 prefix",
        pattern=r"^/[a-zA-Z0-9_-]+$",
    )
    DEBUG_MODE: bool = Field(default=False, description="Debug mode flag")
    ENVIRONMENT: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Deployment environment",
    )


class SecuritySettings(BaseModel):
    """Security-related settings."""
    SECRET_KEY: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description="Secret key for JWT tokens",
        min_length=32,
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440,  # 24 hours
        description="JWT token expiration in minutes",
        gt=0,
        le=44640,
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30,
        description="Refresh token expiration in days",
        gt=0,
        le=90,
    )
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    ALLOWED_HOSTS: List[str] = Field(
        default=["localhost", "127.0.0.1"],
        description="Allowed hosts",
        min_items=1,
    )
    # New password-related settings
    MIN_PASSWORD_LENGTH: int = Field(
        default=8,
        description="Minimum password length",
        gt=0,
        le=128,
    )
    MAX_PASSWORD_LENGTH: int = Field(
        default=128,
        description="Maximum password length",
        gt=8,
        le=256,
    )
    MIN_PASSWORD_COMPLEXITY: int = Field(
        default=3,
        description="Minimum password complexity score (out of 5)",
        ge=0,
        le=5,
    )
    # Login tracking settings
    MAX_LOGIN_ATTEMPTS: int = Field(
        default=5,
        description="Maximum failed login attempts before lockout",
        gt=0,
        le=20,
    )
    LOCKOUT_MINUTES: int = Field(
        default=30,
        description="Duration in minutes for account lockout after max failures",
        gt=0,
        le=1440,
    )
    ATTEMPT_WINDOW_MINUTES: int = Field(
        default=5,
        description="Time window in minutes for tracking login attempts",
        gt=0,
        le=60,
    )


class DatabaseSettings(BaseModel):
    """Database connection settings."""
    MONGODB_URL: MongoDBUrl = Field(..., description="MongoDB connection URI")
    MONGODB_DB_NAME: str = Field(
        default="trading_db",
        description="MongoDB database name",
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    MONGODB_MAX_CONNECTIONS: int = Field(
        default=10,
        description="Maximum MongoDB connections",
        gt=0,
        le=100,
    )
    MONGODB_MIN_CONNECTIONS: int = Field(
        default=1,
        description="Minimum MongoDB connections",
        gt=0,
    )
    MONGODB_TIMEOUT_MS: int = Field(
        default=5000,
        description="MongoDB timeout in milliseconds",
        gt=0,
        le=30000,
    )

    @model_validator(mode="after")
    def check_connections(cls, values):
        """Validate min connections <= max connections."""
        min_conn = values.MONGODB_MIN_CONNECTIONS
        max_conn = values.MONGODB_MAX_CONNECTIONS
        if min_conn is not None and max_conn is not None and min_conn > max_conn:
            raise ConfigValidationError(
                "MONGODB_MIN_CONNECTIONS cannot be greater than MONGODB_MAX_CONNECTIONS"
            )
        return values

    def get_database_config(self) -> DatabaseConfig:
        """Return a dictionary with database configuration."""
        return {
            "url": self.MONGODB_URL,
            "db_name": self.MONGODB_DB_NAME,
            "max_connections": self.MONGODB_MAX_CONNECTIONS,
            "min_connections": self.MONGODB_MIN_CONNECTIONS,
            "timeout_ms": self.MONGODB_TIMEOUT_MS,
        }


class RedisSettings(BaseModel):
    """Redis and cache-related settings."""
    REDIS_URL: RedisUrl = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URI",
    )
    REDIS_TIMEOUT: int = Field(
        default=5,
        description="Redis timeout in seconds",
        gt=0,
        le=30,
    )
    RATE_LIMIT_REDIS_URL: RedisUrl = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for rate limiting",
    )
    REDIS_CACHE_URL: RedisUrl = Field(
        default="redis://localhost:6379/1",
        description="Redis URL for general caching",
    )
    REDIS_CACHE_TTL: int = Field(
        default=3600,
        description="Default TTL for cached items (1 hour)",
        gt=0,
    )
    # Added settings for auth services
    TOKEN_BLACKLIST_PREFIX: str = Field(
        default="token_blacklist:",
        description="Redis key prefix for token blacklist",
    )
    LOGIN_ATTEMPT_PREFIX: str = Field(
        default="login_attempt:",
        description="Redis key prefix for login attempts",
    )
    LOGIN_LOCKOUT_PREFIX: str = Field(
        default="login_lockout:",
        description="Redis key prefix for account lockouts",
    )


class CorsSettings(BaseModel):
    """CORS configuration."""
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=[], description="CORS allowed origins"
    )

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def parse_cors_origins(cls, v: Union[str, List[str]]):
        """Parse CORS origins from various input formats."""
        if isinstance(v, str):
            v = v.strip()
            # If the string starts with a bracket, assume it's a JSON list.
            if v.startswith("["):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed]
                    raise ConfigValidationError("Parsed CORS origins must be a list")
                except json.JSONDecodeError:
                    raise ConfigValidationError("Invalid JSON format for BACKEND_CORS_ORIGINS")
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v]
        raise ConfigValidationError("Invalid CORS origins format")


class ErrorHandlingSettings(BaseModel):
    """Error handling and recovery configuration."""
    ERROR_NOTIFICATION_LEVELS: Set[ErrorLevel] = Field(
        default={ErrorLevel.CRITICAL, ErrorLevel.HIGH},
        description="Error levels that trigger notifications",
    )
    ERROR_NOTIFICATION_COOLDOWN: int = Field(
        default=300,
        description="Cooldown between error notifications (seconds)",
        gt=0,
        le=3600,
    )
    ERROR_RETRY_ATTEMPTS: int = Field(
        default=3,
        description="Number of retry attempts for recoverable errors",
        gt=0,
        le=10,
    )
    ERROR_RETRY_DELAY: int = Field(
        default=1,
        description="Base delay between retries (seconds)",
        gt=0,
        le=60,
    )
    ERROR_RECOVERY_STRATEGIES: Dict[str, RecoveryStrategy] = Field(
        default_factory=lambda: {
            "RateLimitError": RecoveryStrategy.WAIT_AND_RETRY,
            "WebSocketError": RecoveryStrategy.RECONNECT,
            "DatabaseError": RecoveryStrategy.RETRY_WITH_BACKOFF,
        },
        description="Error recovery strategy mapping",
    )
    # New settings for lock cleanup
    ERROR_LOCK_MAX_AGE: int = Field(
        default=300,  # 5 minutes
        description="Maximum age for error recovery locks (seconds)",
        gt=0,
    )
    ERROR_LOCK_CLEANUP_INTERVAL: int = Field(
        default=60,  # 1 minute
        description="Interval for cleaning up stale locks (seconds)",
        gt=0,
    )
    # Batch processing settings
    ERROR_BATCH_SIZE: int = Field(
        default=10,
        description="Number of errors to process in batch",
        gt=0,
    )
    ERROR_BATCH_INTERVAL: int = Field(
        default=1,  # 1 second
        description="Interval for batch processing (seconds)",
        gt=0,
    )

    def get_notification_config(self) -> NotificationConfig:
        """Return a notification configuration dictionary."""
        return {
            "levels": list(self.ERROR_NOTIFICATION_LEVELS),
            "cooldown": self.ERROR_NOTIFICATION_COOLDOWN,
            "telegram_enabled": True,  # Adjust based on actual Telegram token presence.
        }

    def get_error_recovery_strategy(self, error_type: str) -> Optional[RecoveryStrategy]:
        """Get recovery strategy for a specific error type."""
        return self.ERROR_RECOVERY_STRATEGIES.get(error_type)

    def should_notify_error(self, error_level: ErrorLevel) -> bool:
        """Determine if an error level should trigger notifications."""
        return error_level in self.ERROR_NOTIFICATION_LEVELS


class LoggingSettings(BaseModel):
    """Logging configuration."""
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, description="Logging level")
    LOG_FORMAT: str = Field(default="json", description="Log format (json/text)")
    LOG_FILE_PATH: Path = Field(default=Path("logs/app.log"), description="Log file path")
    ERROR_LOG_FILE_PATH: Path = Field(
        default=Path("logs/error.log"), 
        description="Error log file path"
    )
    MAX_LOG_SIZE: int = Field(
        default=10485760,  # 10MB
        description="Max log file size in bytes",
        gt=0,
    )
    MAX_LOG_BACKUPS: int = Field(
        default=5,
        description="Number of log file backups to retain",
        gt=0,
    )
    CONSOLE_LOGGING: bool = Field(
        default=True,
        description="Enable console logging"
    )
    USE_COLORS: bool = Field(
        default=True,
        description="Use colors in text log format"
    )

    @validator("LOG_LEVEL", pre=True)
    def validate_log_level(cls, v: Union[str, LogLevel]) -> LogLevel:
        """Convert string log levels to enum values."""
        if isinstance(v, str):
            try:
                return LogLevel(v.upper())
            except ValueError:
                valid_levels = [e.value for e in LogLevel]
                raise ConfigValidationError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v


class RateLimitingSettings(BaseModel):
    """Rate limiting configuration."""
    RATE_LIMIT_TRADES_PER_MINUTE: int = Field(
        default=30, description="Maximum trades per minute", gt=0
    )
    RATE_LIMIT_ORDERS_PER_SECOND: int = Field(
        default=5, description="Maximum orders per second", gt=0
    )


class WebhookSettings(BaseModel):
    """Webhook configuration."""
    TRADINGVIEW_WEBHOOK_SECRET: SecretStr = Field(
        ..., description="TradingView webhook secret"
    )
    WEBHOOK_FORWARD_URL: Optional[str] = Field(
        default=None, description="Webhook forwarding URL"
    )
    WEBHOOK_TIMEOUT: int = Field(
        default=30,
        description="Webhook timeout in seconds",
        gt=0,
        le=300,
    )

    @validator("TRADINGVIEW_WEBHOOK_SECRET", pre=True)
    def validate_webhook_secret(cls, v: Union[str, SecretStr]) -> SecretStr:
        """Validate webhook secret meets minimum requirements."""
        return validate_secret(v, min_length=20)


class TelegramSettings(BaseModel):
    """Telegram notification configuration."""
    TELEGRAM_BOT_TOKEN: SecretStr = Field(..., description="Telegram bot token")
    TELEGRAM_CHAT_ID: str = Field(
        ..., description="Telegram chat ID", pattern=r"^-?\d+$"
    )
    TELEGRAM_MESSAGE_QUEUE_SIZE: int = Field(
        default=1000, description="Telegram message queue size", gt=0
    )
    TELEGRAM_RETRY_DELAY: int = Field(
        default=5, description="Retry delay for failed messages (in seconds)", gt=0
    )

    @validator("TELEGRAM_BOT_TOKEN", pre=True)
    def validate_telegram_token(cls, v: Union[str, SecretStr]) -> SecretStr:
        """Validate Telegram bot token format."""
        token = v.get_secret_value() if isinstance(v, SecretStr) else v
        if not token or not token.strip():
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN cannot be empty")
        if len(token.split(":")) != 2:
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN must be in format 'botid:token'")
        return SecretStr(token)


class CronSettings(BaseModel):
    """Scheduled job configuration."""
    DAILY_PERFORMANCE_CRON: str = Field(
        default="0 0 * * *", description="Cron schedule for daily performance calculations"
    )
    TRADING_HISTORY_CRON: str = Field(
        default="0 0 * * *", description="Cron schedule for trading history updates"
    )
    BALANCE_SYNC_CRON: str = Field(
        default="0 */6 * * *", description="Cron schedule for balance synchronization"
    )
    CLEANUP_CRON: str = Field(
        default="0 0 * * *", description="Cron schedule for cleanup tasks"
    )
    SYMBOL_VERIFICATION_CRON: str = Field(
        default="0 0 * * 0", description="Cron schedule for symbol verification"
    )


class BalanceSyncSettings(BaseModel):
    """Balance synchronization configuration."""
    BALANCE_SYNC_MAX_RETRIES: int = Field(
        default=5, description="Max retries for balance sync", gt=0
    )
    BALANCE_SYNC_RETRY_DELAY: int = Field(
        default=10, description="Delay between retries in seconds", gt=0
    )
    BALANCE_ERROR_THRESHOLD: int = Field(
        default=10,
        description="Max errors before marking account inactive",
        gt=0,
    )
    # New batch processing setting
    BALANCE_SYNC_BATCH_SIZE: int = Field(
        default=20,
        description="Number of accounts to sync in one batch",
        gt=0,
    )


class TradingHoursSettings(BaseModel):
    """Trading hours restrictions configuration."""
    ENABLE_TRADING_HOURS: bool = Field(
        default=False, description="Enable trading hour restrictions"
    )
    TRADING_HOURS_START: int = Field(
        default=0, description="Start time in 24-hour format", ge=0, le=24
    )
    TRADING_HOURS_END: int = Field(
        default=24, description="End time in 24-hour format", ge=0, le=24
    )
    TRADING_TIMEZONE: str = Field(
        default="UTC", description="Timezone for trading hours"
    )


class WebsocketSettings(BaseModel):
    """WebSocket connection configuration."""
    WS_MAX_CONNECTIONS: int = Field(
        default=1000, description="Max concurrent WebSocket connections", gt=0
    )
    WS_HEARTBEAT_INTERVAL: int = Field(
        default=30,
        description="WebSocket heartbeat interval (seconds)",
        gt=0,
    )
    WS_RECONNECT_DELAY: int = Field(
        default=5,
        description="Delay for WebSocket reconnections (seconds)",
        gt=0,
    )
    # New settings for WebSocket optimization
    WS_CONNECTION_POOL_SIZE: int = Field(
        default=20,
        description="Size of the WebSocket connection pool",
        gt=0,
    )
    WS_TIMEOUT: int = Field(
        default=60,
        description="WebSocket operation timeout (seconds)",
        gt=0,
    )


class ExchangeSettings(BaseModel):
    """Exchange API configuration."""
    DEFAULT_TESTNET: bool = Field(
        default=True,
        description="Enable testnet by default for exchanges",
    )
    EXCHANGE_API_TIMEOUT: int = Field(
        default=10000,
        description="Exchange API timeout in milliseconds",
        gt=0,
    )
    ORDER_MONITOR_INTERVAL: float = Field(
        default=0.5,
        description="Interval for order monitoring (seconds)",
        gt=0,
    )
    POSITION_MONITOR_INTERVAL: float = Field(
        default=1.0,
        description="Interval for position monitoring (seconds)",
        gt=0,
    )
    MAX_ORDER_ATTEMPTS: int = Field(
        default=5,
        description="Maximum attempts for order adjustments",
        gt=0,
    )
    POSITION_CLEANUP_INTERVAL: int = Field(
        default=300,
        description="Cleanup interval for inactive positions (seconds)",
        gt=0,
    )
    MAX_LEVERAGE: int = Field(
        default=100,
        description="Maximum allowed leverage",
        gt=0,
    )
    MAX_RISK_PERCENTAGE: float = Field(
        default=5.0,
        description="Maximum risk per trade",
        gt=0.0,
        le=100.0,
    )
    # New connection pooling setting
    CONNECTION_POOL_SIZE: int = Field(
        default=20,
        description="Size of the HTTP connection pool per exchange",
        gt=0,
    )


class PerformanceSettings(BaseModel):
    """Performance tracking configuration."""
    PERFORMANCE_RECORD_RETENTION_DAYS: int = Field(
        default=365,
        description="Days to keep performance records",
        gt=0,
    )
    PERFORMANCE_SYNC_BATCH_SIZE: int = Field(
        default=500,
        description="Number of records to process in batch",
        gt=0,
    )
    PERFORMANCE_MAX_PARALLEL_UPDATES: int = Field(
        default=10,
        description="Max parallel performance updates",
        gt=0,
    )


class MonitoringSettings(BaseModel):
    """System monitoring configuration."""
    ENABLE_METRICS: bool = Field(
        default=True, description="Enable Prometheus metrics"
    )
    METRICS_PORT: int = Field(
        default=9090,
        description="Port for Prometheus metrics",
        gt=0,
        le=65535,
    )
    ENABLE_PERFORMANCE_MONITORING: bool = Field(
        default=True, description="Enable performance monitoring"
    )
    METRICS_COLLECTION_INTERVAL: int = Field(
        default=60,
        description="Metrics collection interval in seconds",
        gt=0,
    )
    HEALTH_CHECK_INTERVAL: int = Field(
        default=60,
        description="Health check interval (seconds)",
        gt=0,
    )


class DevelopmentSettings(BaseModel):
    """Development-specific settings."""
    ENABLE_DEV_FEATURES: bool = Field(
        default=False, description="Enable development-only features"
    )
    RELOAD_SETTINGS_ON_CHANGE: bool = Field(
        default=False, description="Reload settings when env files change"
    )
    MOCK_EXTERNAL_SERVICES: bool = Field(
        default=False, description="Use mock implementations for external services"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Settings Model
# ─────────────────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """Main settings container with lazy-loaded nested models."""
    # Use lazy initialization for nested models to improve performance
    app: AppSettings = Field(default_factory=AppSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    database: DatabaseSettings
    redis: RedisSettings = Field(default_factory=RedisSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    error: ErrorHandlingSettings = Field(default_factory=ErrorHandlingSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    rate_limiting: RateLimitingSettings = Field(default_factory=RateLimitingSettings)
    webhook: WebhookSettings
    telegram: TelegramSettings
    cron: CronSettings = Field(default_factory=CronSettings)
    balance_sync: BalanceSyncSettings = Field(default_factory=BalanceSyncSettings)
    trading_hours: TradingHoursSettings = Field(default_factory=TradingHoursSettings)
    websocket: WebsocketSettings = Field(default_factory=WebsocketSettings)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    development: DevelopmentSettings = Field(default_factory=DevelopmentSettings)

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env.development",
        env_nested_delimiter="__",
        validate_assignment=True,
        extra="ignore",
    )

    def get_database_config(self) -> DatabaseConfig:
        """Get database configuration dictionary."""
        return self.database.get_database_config()

    def get_notification_config(self) -> NotificationConfig:
        """Get notification configuration dictionary."""
        return self.error.get_notification_config()

    def get_error_recovery_strategy(self, error_type: str) -> Optional[RecoveryStrategy]:
        """Get recovery strategy for a specific error type."""
        return self.error.get_error_recovery_strategy(error_type)

    def should_notify_error(self, error_level: ErrorLevel) -> bool:
        """Determine if an error level should trigger notifications."""
        return self.error.should_notify_error(error_level)

    @classmethod
    def reload(cls) -> None:
        """Force reload settings by clearing the cache."""
        get_settings.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Settings Instance Management
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of the settings."""
    env_file = os.environ.get("ENV_FILE", ".env.development")
    return Settings(_env_file=env_file)


# Global settings instance with lazy loading
settings = get_settings()


# Optional file watcher for development environments
if settings.development.RELOAD_SETTINGS_ON_CHANGE:
    try:
        import watchdog.events
        import watchdog.observers
        
        class EnvFileHandler(watchdog.events.FileSystemEventHandler):
            def on_modified(self, event):
                if Path(event.src_path).name.startswith(".env"):
                    Settings.reload()
                    print(f"Settings reloaded due to changes in {event.src_path}")
        
        # Start watching the env file
        path = Path(os.environ.get("ENV_FILE", ".env.development")).parent
        event_handler = EnvFileHandler()
        observer = watchdog.observers.Observer()
        observer.schedule(event_handler, path=str(path), recursive=False)
        observer.start()
    except ImportError:
        # watchdog not installed, skipping file watching
        pass