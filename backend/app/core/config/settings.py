"""
Refactored settings module for FastAPI using Pydantic v2.
"""

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Set, Dict, Union

from pydantic import (
    BaseModel,
    Field,
    model_validator,
    validator,
    SecretStr,
    constr,
)
from pydantic_settings import BaseSettings

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
    token = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not token or len(token) < min_length:
        raise ValueError(f"Secret must be at least {min_length} characters long.")
    return SecretStr(token)

# ─────────────────────────────────────────────────────────────────────────────
# Nested Models for Different Configuration Areas
# ─────────────────────────────────────────────────────────────────────────────

class AppSettings(BaseModel):
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


class DatabaseSettings(BaseModel):
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
        min_conn = values.MONGODB_MIN_CONNECTIONS
        max_conn = values.MONGODB_MAX_CONNECTIONS
        if min_conn is not None and max_conn is not None and min_conn > max_conn:
            raise ConfigValidationError(
                "MONGODB_MIN_CONNECTIONS cannot be greater than MONGODB_MAX_CONNECTIONS"
            )
        return values

    def get_database_config(self) -> DatabaseConfig:
        return {
            "url": self.MONGODB_URL,
            "db_name": self.MONGODB_DB_NAME,
            "max_connections": self.MONGODB_MAX_CONNECTIONS,
            "min_connections": self.MONGODB_MIN_CONNECTIONS,
            "timeout_ms": self.MONGODB_TIMEOUT_MS,
        }


class RedisSettings(BaseModel):
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


class CorsSettings(BaseModel):
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=[], description="CORS allowed origins"
    )

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def parse_cors_origins(cls, v: Union[str, List[str]]):
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

    def get_notification_config(self) -> NotificationConfig:
        return {
            "levels": list(self.ERROR_NOTIFICATION_LEVELS),
            "cooldown": self.ERROR_NOTIFICATION_COOLDOWN,
            "telegram_enabled": True,  # Adjust based on actual Telegram token presence.
        }

    def get_error_recovery_strategy(self, error_type: str) -> Optional[RecoveryStrategy]:
        return self.ERROR_RECOVERY_STRATEGIES.get(error_type)

    def should_notify_error(self, error_level: ErrorLevel) -> bool:
        return error_level in self.ERROR_NOTIFICATION_LEVELS


class LoggingSettings(BaseModel):
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO, description="Logging level")
    LOG_FORMAT: str = Field(default="json", description="Log format (json/text)")
    LOG_FILE_PATH: Path = Field(default=Path("logs/app.log"), description="Log file path")
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

    @validator("LOG_LEVEL", pre=True)
    def validate_log_level(cls, v: Union[str, LogLevel]) -> LogLevel:
        if isinstance(v, str):
            try:
                return LogLevel(v.upper())
            except ValueError:
                valid_levels = [e.value for e in LogLevel]
                raise ConfigValidationError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v


class RateLimitingSettings(BaseModel):
    RATE_LIMIT_TRADES_PER_MINUTE: int = Field(
        default=30, description="Maximum trades per minute", gt=0
    )
    RATE_LIMIT_ORDERS_PER_SECOND: int = Field(
        default=5, description="Maximum orders per second", gt=0
    )


class WebhookSettings(BaseModel):
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
        return validate_secret(v, min_length=20)


class TelegramSettings(BaseModel):
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
        token = v.get_secret_value() if isinstance(v, SecretStr) else v
        if not token or not token.strip():
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN cannot be empty")
        if len(token.split(":")) != 2:
            raise ConfigValidationError("TELEGRAM_BOT_TOKEN must be in format 'botid:token'")
        return SecretStr(token)


class CronSettings(BaseModel):
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


class TradingHoursSettings(BaseModel):
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


class ExchangeSettings(BaseModel):
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


class PerformanceSettings(BaseModel):
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
    ENABLE_DEV_FEATURES: bool = Field(
        default=False, description="Enable development-only features"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Settings Model
# ─────────────────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
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

    class Config:
        case_sensitive = False
        env_file = ".env.development"
        env_nested_delimiter = "__"
        validate_assignment = True
        extra = "ignore"

    def get_database_config(self) -> DatabaseConfig:
        return self.database.get_database_config()

    def get_notification_config(self) -> NotificationConfig:
        return self.error.get_notification_config()

    def get_error_recovery_strategy(self, error_type: str) -> Optional[RecoveryStrategy]:
        return self.error.get_error_recovery_strategy(error_type)

    def should_notify_error(self, error_level: ErrorLevel) -> bool:
        return self.error.should_notify_error(error_level)


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of the settings."""
    return Settings()


# Global settings instance
settings = get_settings()
