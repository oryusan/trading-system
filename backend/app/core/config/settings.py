"""
Enhanced configuration system with comprehensive error handling and validation.

Features:
- Environment-based configuration
- Strong validation
- Secure defaults
- Error handling integration
- Runtime configuration updates
"""

from typing import List, Optional, Dict, Any, Union, Set, Literal
from datetime import timedelta
import secrets
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, ValidationError, AnyHttpUrl, SecretStr
from functools import lru_cache
import os
from pathlib import Path

from app.core.references import (
    LogLevel,
    ErrorLevel,
    RecoveryStrategy,
    DatabaseConfig,
    NotificationConfig,
    ConfigValidationError,
    SettingsType,
    Environment,
    RecoveryTimeouts,
    LogRotation,
    RateLimits,
    ExchangeTimeouts,
    CacheSettings,
    MonitoringSettings
)

class Settings(BaseSettings, SettingsType):
    """Application settings with enhanced validation and error handling."""

    # Application Settings
    PROJECT_NAME: str = Field(
        default="Trading WebApp",
        description="Name of the project",
        min_length=1,
        max_length=100
    )
    VERSION: str = Field(
        default="1.0.0",
        description="API version",
        pattern=r"^\d+\.\d+\.\d+$"
    )
    API_V1_STR: str = Field(
        default="/api/v1",
        description="API v1 prefix",
        pattern=r"^/[a-zA-Z0-9_-]+$"
    )
    DEBUG_MODE: bool = Field(
        default=False,
        description="Debug mode flag"
    )
    ENVIRONMENT: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Deployment environment"
    )

    # Security Settings
    SECRET_KEY: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description="Secret key for JWT tokens",
        min_length=32
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440,  # 24 hours
        description="JWT token expiration in minutes",
        gt=0,
        le=44640  # 31 days max
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30,
        description="Refresh token expiration in days",
        gt=0,
        le=90  # 90 days max
    )
    ALGORITHM: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256",
        description="JWT algorithm"
    )
    ALLOWED_HOSTS: List[str] = Field(
        default=["localhost", "127.0.0.1"],
        description="Allowed hosts",
        min_items=1
    )

    # Database Settings
    MONGODB_URL: str = Field(
        ...,  # Required field
        description="MongoDB connection URI",
        pattern=r"^mongodb(\+srv)?://.*"
    )
    MONGODB_DB_NAME: str = Field(
        default="trading_db",
        description="MongoDB database name",
        min_length=1,
        max_length=63,
        pattern=r"^[a-zA-Z0-9_-]+$"
    )
    MONGODB_MAX_CONNECTIONS: int = Field(
        default=10,
        description="Maximum MongoDB connections",
        gt=0,
        le=100
    )
    MONGODB_MIN_CONNECTIONS: int = Field(
        default=1,
        description="Minimum MongoDB connections",
        gt=0
    )
    MONGODB_TIMEOUT_MS: int = Field(
        default=5000,
        description="MongoDB timeout in milliseconds",
        gt=0,
        le=30000
    )

    # Redis Settings
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URI",
        pattern=r"^redis://.*"
    )
    REDIS_TIMEOUT: int = Field(
        default=5,
        description="Redis timeout in seconds",
        gt=0,
        le=30
    )
    RATE_LIMIT_REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for rate limiting",
        pattern=r"^redis://.*"
    )
    REDIS_CACHE_URL: str = Field(
        default="redis://localhost:6379/1",
        description="Redis URL for general caching",
        pattern=r"^redis://.*"
    )
    REDIS_CACHE_TTL: int = Field(
        default=3600,
        description="Default TTL for cached items (1 hour)",
        gt=0
    )

    # CORS Settings
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=[],
        description="CORS allowed origins"
    )

    # Error Handling Settings
    ERROR_NOTIFICATION_LEVELS: Set[ErrorLevel] = Field(
        default={ErrorLevel.CRITICAL, ErrorLevel.HIGH},
        description="Error levels that trigger notifications"
    )
    ERROR_NOTIFICATION_COOLDOWN: int = Field(
        default=300,
        description="Cooldown between error notifications (seconds)",
        gt=0,
        le=3600
    )
    ERROR_RETRY_ATTEMPTS: int = Field(
        default=3,
        description="Number of retry attempts for recoverable errors",
        gt=0,
        le=10
    )
    ERROR_RETRY_DELAY: int = Field(
        default=1,
        description="Base delay between retries (seconds)",
        gt=0,
        le=60
    )
    ERROR_RECOVERY_STRATEGIES: Dict[str, RecoveryStrategy] = Field(
        default_factory=lambda: {
            "RateLimitError": RecoveryStrategy.WAIT_AND_RETRY,
            "WebSocketError": RecoveryStrategy.RECONNECT,
            "DatabaseError": RecoveryStrategy.RETRY_WITH_BACKOFF
        },
        description="Error recovery strategy mapping"
    )

    # Logging Settings
    LOG_LEVEL: LogLevel = Field(
        default=LogLevel.INFO,
        description="Logging level"
    )
    LOG_FORMAT: Literal["json", "text"] = Field(
        default="json",
        description="Log format (json/text)"
    )
    LOG_FILE_PATH: Path = Field(
        default=Path("logs/app.log"),
        description="Log file path"
    )
    MAX_LOG_SIZE: int = Field(
        default=10485760,  # 10MB
        description="Max log file size in bytes",
        gt=0
    )
    MAX_LOG_BACKUPS: int = Field(
        default=5,
        description="Number of log file backups to retain",
        gt=0
    )

    # API Rate Limiting
    RATE_LIMIT_TRADES_PER_MINUTE: int = Field(
        default=30,
        description="Maximum trades per minute",
        gt=0
    )
    RATE_LIMIT_ORDERS_PER_SECOND: int = Field(
        default=5,
        description="Maximum orders per second",
        gt=0
    )

    # Webhook Settings
    TRADINGVIEW_WEBHOOK_SECRET: SecretStr = Field(
        ...,  # Required field
        description="TradingView webhook secret"
    )
    WEBHOOK_FORWARD_URL: Optional[str] = Field(
        default=None,
        description="Webhook forwarding URL"
    )
    WEBHOOK_TIMEOUT: int = Field(
        default=30,
        description="Webhook timeout in seconds",
        gt=0,
        le=300
    )

    # Telegram Settings
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        ...,  # Required field
        description="Telegram bot token"
    )
    TELEGRAM_CHAT_ID: str = Field(
        ...,  # Required field
        description="Telegram chat ID",
        pattern=r"^-?\d+$"
    )
    TELEGRAM_MESSAGE_QUEUE_SIZE: int = Field(
        default=1000,
        description="Telegram message queue size",
        gt=0
    )
    TELEGRAM_RETRY_DELAY: int = Field(
        default=5,
        description="Retry delay for failed messages (in seconds)",
        gt=0
    )

    # Cron Job Settings
    DAILY_PERFORMANCE_CRON: str = Field(
        default="0 0 * * *",  # At midnight every day
        description="Cron schedule for daily performance calculations"
    )
    TRADING_HISTORY_CRON: str = Field(
        default="0 0 * * *",  # At midnight every day
        description="Cron schedule for trading history updates"
    )
    BALANCE_SYNC_CRON: str = Field(
        default="0 */6 * * *",  # Every 6 hours
        description="Cron schedule for balance synchronization"
    )
    CLEANUP_CRON: str = Field(
        default="0 0 * * *",  # At midnight every day
        description="Cron schedule for cleanup tasks"
    )
    SYMBOL_VERIFICATION_CRON: str = Field(
        default="0 0 * * 0",  # Every Sunday at midnight
        description="Cron schedule for symbol verification"
    )

    # Balance Sync Settings
    BALANCE_SYNC_MAX_RETRIES: int = Field(
        default=5,
        description="Max retries for balance sync",
        gt=0
    )
    BALANCE_SYNC_RETRY_DELAY: int = Field(
        default=10,
        description="Delay between retries in seconds",
        gt=0
    )
    BALANCE_ERROR_THRESHOLD: int = Field(
        default=10,
        description="Max errors before marking account inactive",
        gt=0
    )

    # Trading Hours Settings
    ENABLE_TRADING_HOURS: bool = Field(
        default=False,
        description="Enable trading hour restrictions"
    )
    TRADING_HOURS_START: int = Field(
        default=0,
        description="Start time in 24-hour format",
        ge=0,
        le=24
    )
    TRADING_HOURS_END: int = Field(
        default=24,
        description="End time in 24-hour format",
        ge=0,
        le=24
    )
    TRADING_TIMEZONE: str = Field(
        default="UTC",
        description="Timezone for trading hours"
    )

    # WebSocket Settings
    WS_MAX_CONNECTIONS: int = Field(
        default=1000,
        description="Max concurrent WebSocket connections",
        gt=0
    )
    WS_HEARTBEAT_INTERVAL: int = Field(
        default=30,
        description="WebSocket heartbeat interval (seconds)",
        gt=0
    )
    WS_RECONNECT_DELAY: int = Field(
        default=5,
        description="Delay for WebSocket reconnections (seconds)",
        gt=0
    )

    # Exchange Settings
    DEFAULT_TESTNET: bool = Field(
        default=True,
        description="Enable testnet by default for exchanges"
    )
    EXCHANGE_API_TIMEOUT: int = Field(
        default=10000,
        description="Exchange API timeout in milliseconds",
        gt=0
    )
    ORDER_MONITOR_INTERVAL: float = Field(
        default=0.5,
        description="Interval for order monitoring (seconds)",
        gt=0
    )
    POSITION_MONITOR_INTERVAL: float = Field(
        default=1.0,
        description="Interval for position monitoring (seconds)",
        gt=0
    )
    MAX_ORDER_ATTEMPTS: int = Field(
        default=5,
        description="Maximum attempts for order adjustments",
        gt=0
    )
    POSITION_CLEANUP_INTERVAL: int = Field(
        default=300,
        description="Cleanup interval for inactive positions (seconds)",
        gt=0
    )
    MAX_LEVERAGE: int = Field(
        default=100,
        description="Maximum allowed leverage",
        gt=0
    )
    MAX_RISK_PERCENTAGE: float = Field(
        default=5.0,
        description="Maximum risk per trade",
        gt=0.0,
        le=100.0
    )

    # Performance Settings
    PERFORMANCE_RECORD_RETENTION_DAYS: int = Field(
        default=365,
        description="Days to keep performance records",
        gt=0
    )
    PERFORMANCE_SYNC_BATCH_SIZE: int = Field(
        default=500,
        description="Number of records to process in batch",
        gt=0
    )
    PERFORMANCE_MAX_PARALLEL_UPDATES: int = Field(
        default=10,
        description="Max parallel performance updates",
        gt=0
    )

    # Monitoring Settings
    ENABLE_METRICS: bool = Field(
        default=True,
        description="Enable Prometheus metrics"
    )
    METRICS_PORT: int = Field(
        default=9090,
        description="Port for Prometheus metrics",
        gt=0,
        le=65535
    )
    ENABLE_PERFORMANCE_MONITORING: bool = Field(
        default=True,
        description="Enable performance monitoring"
    )
    METRICS_COLLECTION_INTERVAL: int = Field(
        default=60,
        description="Metrics collection interval in seconds",
        gt=0
    )
    HEALTH_CHECK_INTERVAL: int = Field(
        default=60,
        description="Health check interval (seconds)",
        gt=0
    )

    # Development Features
    ENABLE_DEV_FEATURES: bool = Field(
        default=False,
        description="Enable development-only features"
    )

    @field_validator("MONGODB_MIN_CONNECTIONS")
    @classmethod
    def validate_min_connections(cls, v: int, info: Dict[str, Any]) -> int:
        """Validate minimum connections."""
        max_conn = info.data.get("MONGODB_MAX_CONNECTIONS")
        if max_conn is not None and v > max_conn:
            raise ConfigValidationError("MIN_CONNECTIONS cannot be greater than MAX_CONNECTIONS")
        return v

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        """Process CORS origins configuration."""
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ConfigValidationError("Invalid CORS origins format")

    @field_validator("TELEGRAM_BOT_TOKEN", mode="before")
    @classmethod
    def validate_telegram_token(cls, v: str) -> str:
        """Validate Telegram bot token format and presence."""
        if not v or not v.strip():
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN cannot be empty")
        
        if len(v.split(":")) != 2:
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN must be in format 'botid:token'")
        
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> LogLevel:
        """Validate logging level."""
        try:
            return LogLevel(v.upper())
        except ValueError:
            raise ConfigValidationError(f"Invalid log level. Must be one of {list(LogLevel)}")

    @field_validator("TRADINGVIEW_WEBHOOK_SECRET", mode="before")
    @classmethod
    def validate_webhook_secret(cls, v: str) -> str:
        """Ensure TRADINGVIEW_WEBHOOK_SECRET is provided and strong."""
        if not v or len(v) < 20:
            raise ValueError("TRADINGVIEW_WEBHOOK_SECRET must be at least 20 characters long.")
        return v

    def get_database_config(self) -> DatabaseConfig:
        """Get database configuration."""
        return {
            "url": self.MONGODB_URL,
            "db_name": self.MONGODB_DB_NAME,
            "max_connections": self.MONGODB_MAX_CONNECTIONS,
            "min_connections": self.MONGODB_MIN_CONNECTIONS,
            "timeout_ms": self.MONGODB_TIMEOUT_MS
        }

    def get_notification_config(self) -> NotificationConfig:
        """Get notification configuration."""
        return {
            "levels": list(self.ERROR_NOTIFICATION_LEVELS),
            "cooldown": self.ERROR_NOTIFICATION_COOLDOWN,
            "telegram_enabled": bool(self.TELEGRAM_BOT_TOKEN.get_secret_value())
        }

    def get_error_recovery_strategy(self, error_type: str) -> Optional[RecoveryStrategy]:
        """Get recovery strategy for an error type."""
        return self.ERROR_RECOVERY_STRATEGIES.get(error_type)

    def should_notify_error(self, error_level: ErrorLevel) -> bool:
        """Check if an error level should trigger notifications."""
        return error_level in self.ERROR_NOTIFICATION_LEVELS

    model_config = {
        "case_sensitive": True,
        "env_file": ".env.development",
        "validate_assignment": True,
        "extra": "ignore"
    }

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

# Global settings instance
settings = get_settings()