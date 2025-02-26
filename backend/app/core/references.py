"""
Central reference point for core module interfaces and type definitions.

This file includes:
- Protocol definitions for logging, database sessions, tokens, trading services, WebSocket managers, performance services, and reference managers.
- Base models for tokens, positions, trades, performance metrics, etc.
- Configuration models for logging rotation, rate limits, exchange timeouts, and more.
- Domain and service type aliases.
- Utility functions for model relationships and service access control.
- Additional definitions (SettingsType, ConfigValidationError).
- TYPE_CHECKING imports.
"""

# Standard Library Imports
from abc import ABC
from datetime import datetime, timedelta
from decimal import Decimal
from typing import (
    Any, Callable, Dict, List, Optional, Set, Type, TypeVar, Union, TYPE_CHECKING, Protocol
)

# Third-Party Imports
from pydantic import BaseModel, SecretStr, Field

# Local Imports
from .enums import (
    Environment,
    ExchangeType,
    UserRole,
    SignalOrderType,
    TimeFrame,
    OrderType,
    TradeSource,
    TradeStatus,
    PositionSide,
    BotStatus,
    PositionStatus,
    PerformanceTimeFrame,
    WebSocketType,
    ConnectionState,
    ErrorLevel,
    ErrorCategory,
    RecoveryStrategy,
    LogLevel,
)

# =============================================================================
# Protocol Definitions
# =============================================================================

class LoggerProtocol(Protocol):
    """Required logging interface."""
    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None: 
        ...
    
    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None: 
        ...
    
    def info(self, message: str, **kwargs: Any) -> None: 
        ...
    def warning(self, message: str, **kwargs: Any) -> None: 
        ...
    def error(self, message: str, **kwargs: Any) -> None: 
        ...
    def critical(self, message: str, **kwargs: Any) -> None: 
        ...

class DatabaseSessionProtocol(Protocol):
    """Database session operations."""
    async def connect(self) -> None: 
        ...
    async def disconnect(self) -> None: 
        ...
    async def commit(self) -> None: 
        ...
    async def rollback(self) -> None: 
        ...
    async def in_transaction(self) -> bool: 
        ...

class TokenProtocol(Protocol):
    """Token data and validation."""
    username: str
    exp: datetime  
    role: Optional[str]
    issued_at: datetime
    token_id: str
    
    def validate_expiry(self) -> None: 
        ...

class TradingServiceProtocol(Protocol):
    """Trading service interface."""
    async def execute_trade(
        self,
        account_id: str,
        symbol: str,
        side: str,
        order_type: OrderType,
        size: str,
        leverage: str,
        take_profit: Optional[str] = None,
        source: TradeSource = TradeSource.TRADING_PANEL
    ) -> Dict[str, Any]: 
        ...

    async def close_position(
        self, 
        symbol: str,
        account_id: str
    ) -> Dict[str, Any]: 
        ...

    async def validate_trade(
        self,
        symbol: str,
        side: str,
        risk_percentage: str,
        leverage: str,
        take_profit: Optional[str] = None
    ) -> Dict[str, bool]: 
        ...

    async def validate_trade_params(
        self,
        symbol: str,
        side: str,
        risk_percentage: float,
        leverage: int,
        take_profit: Optional[str] = None
    ) -> Dict[str, bool]: 
        ...

    async def get_account_balance(
        self,
        account_id: str
    ) -> Dict[str, Union[float, Decimal]]: 
        ...

class WebSocketManagerProtocol(Protocol):
    """WebSocket manager interface."""
    async def get_connection(self, identifier: str) -> Any: 
        ...
    async def create_connection(self, config: Dict[str, Any]) -> Any: 
        ...
    async def close_connection(self, identifier: str) -> None: 
        ...
    async def subscribe(self, identifier: str, channel: str) -> None: 
        ...
    async def unsubscribe(self, identifier: str, channel: str) -> None: 
        ...
    async def is_healthy(self) -> bool: 
        ...
    async def get_status(self) -> Dict[str, Any]: 
        ...

class PerformanceServiceProtocol(Protocol):
    """Performance service interface."""
    async def update_daily_performance(
        self,
        account_id: str,
        date: datetime,
        balance: Decimal,
        equity: Decimal,
        metrics: Dict[str, Union[Decimal, float, int]]
    ) -> None: 
        ...
    
    async def get_account_metrics(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: 
        ...
    
    async def get_group_metrics(
        self,
        group_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: 
        ...

    async def aggregate_performance(
        self,
        account_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        interval: str = "day"
    ) -> Dict[str, Any]: 
        ...
    
    async def calculate_statistics(
        self,
        data: List[Dict[str, Any]]
    ) -> Dict[str, Any]: 
        ...

class ReferenceManagerProtocol(Protocol):
    """Reference manager interface."""
    async def validate_reference(
        self,
        source_type: str,
        target_type: str,
        reference_id: str
    ) -> bool: 
        ...

    async def get_references(
        self,
        source_type: str,
        reference_id: str
    ) -> List[Dict[str, Any]]: 
        ...

    async def add_reference(
        self,
        source_type: str,
        target_type: str,
        source_id: str,
        target_id: str
    ) -> None: 
        ...

    async def remove_reference(
        self,
        source_type: str,
        target_type: str,
        source_id: str,
        target_id: str
    ) -> None: 
        ...

    async def get_service(
        self,
        service_type: str
    ) -> Any: 
        ...

# =============================================================================
# Base Models
# =============================================================================

class BaseTokenData(BaseModel):
    """Base model for token data."""
    username: str
    exp: datetime
    role: Optional[str] = None
    issued_at: datetime
    token_id: str

class UserContext(BaseModel):
    """User context model for authenticated users.
    
    This model represents the key information about an authenticated user,
    including permissions and additional request metadata.
    """
    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="User's username")
    role: str = Field(..., description="User's role (e.g., admin, viewer)")
    permissions: List[str] = Field(default_factory=list, description="List of permissions assigned to the user")
    token_id: str = Field(..., description="Identifier for the authentication token")
    request_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional context for the current request (e.g., IP, path, method, timestamp)"
    )

class DateRange(BaseModel):
    """Date range for queries."""
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD

class BasePosition(BaseModel):
    """Base model for position data."""
    symbol: str
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    leverage: int
    take_profit: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None

class BaseTrade(BaseModel):
    """Base model for trade execution."""
    symbol: str
    side: str
    order_type: OrderType
    size: Decimal
    leverage: int
    risk_percentage: Decimal
    take_profit: Optional[Decimal] = None
    source: TradeSource = TradeSource.TRADING_PANEL

class BasePerformanceMetrics(BaseModel):
    """Base model for performance metrics."""
    total_trades: int
    winning_trades: int
    total_volume: Decimal
    total_pnl: Decimal
    trading_fees: Decimal
    funding_fees: Decimal
    net_pnl: Decimal
    win_rate: float
    roi: float
    drawdown: float

class PerformanceMetrics(BaseModel):
    """Performance calculation results."""
    start_balance: Decimal
    end_balance: Decimal
    total_trades: int
    winning_trades: int
    total_volume: Decimal
    trading_fees: Decimal
    funding_fees: Decimal
    total_pnl: Decimal
    win_rate: float
    roi: float
    drawdown: float
    
    class Config:
        arbitrary_types_allowed = True

# =============================================================================
# Configuration Models
# =============================================================================

class LogRotation(BaseModel):
    """Log rotation settings."""
    max_bytes: int
    backup_count: int
    encoding: str = "utf-8"

class RateLimits(BaseModel):
    """API rate limits."""
    trades_per_minute: int
    orders_per_second: int
    redis_url: str

class ExchangeTimeouts(BaseModel):
    """Exchange operation timeouts."""
    api_timeout_ms: int 
    websocket_timeout_ms: int
    reconnect_delay_ms: int = 5000

class WebhookConfig(BaseModel):
    """Webhook configuration."""
    tradingview_secret: SecretStr
    forward_url: Optional[str] = None
    timeout_seconds: int = 30

class CacheSettings(BaseModel):
    """Cache configuration."""
    redis_url: str
    default_ttl: int = 300
    symbol_info_ttl: int = 3600

class MonitoringSettings(BaseModel):
    """Monitoring configuration."""
    enable_metrics: bool = True
    metrics_port: int = 9090
    health_check_interval: int = 60

# =============================================================================
# Domain / Service Type Aliases
# =============================================================================

# Security-related types:
JWTToken = str
PasswordHash = str
TokenMetadata = Dict[str, Union[str, datetime]]
TokenPayload = Dict[str, Any]
LoginAttemptRecord = Dict[str, List[datetime]]

# Database Types
DatabaseConfig = Dict[str, Union[str, int, float]]
DatabaseError = Dict[str, Any]

# Trading Types
OrderRequest = Dict[str, Any]
OrderResponse = Dict[str, Any]
TradeExecution = Dict[str, Any]
PositionInfo = Dict[str, Any]

# Other Types
ModelState = Dict[str, Any]
ValidationResult = Dict[str, Any]
TimeRange = Dict[str, datetime]
PerformanceDict = Dict[str, float]
TimeSeriesData = List[Dict[str, Any]]

# =============================================================================
# Model Relationships & Service Access Control
# =============================================================================

MODEL_RELATIONSHIPS: Dict[str, Dict[str, List[str]]] = {
    "Account": {
        "has_many": ["Trade", "Position", "DailyPerformance"],
        "belongs_to": ["User", "Bot"],
        "in_groups": ["Group"],
        "tracks": ["PerformanceMetrics"]
    },
    "Bot": {
        "has_many": ["Account"],
        "monitors": ["Position"],
        "executes": ["Trade"]
    },
    "Group": {
        "has_many": ["Account"],
        "aggregates": ["DailyPerformance"],
        "tracks": ["PerformanceMetrics"],
        "accessed_by": ["User"]
    },
    "User": {
        "has_many": ["Account"],
        "accesses": ["Group"],
        "role": ["admin", "exporter", "viewer"]
    },
    "Trade": {
        "belongs_to": ["Account", "Bot"],
        "creates": ["Position"],
        "affects": ["DailyPerformance"]
    },
    "Position": {
        "belongs_to": ["Account", "Trade"],
        "affects": ["DailyPerformance"]
    },
    "DailyPerformance": {
        "belongs_to": ["Account"],
        "tracks": ["PerformanceMetrics"]
    }
}

SERVICE_ACCESS: Dict[str, List[str]] = {
    "Account": ["TradingService", "WebSocketManager", "PerformanceService"],
    "Bot": ["TradingService", "WebSocketManager", "ReferenceManager"],
    "Group": ["PerformanceService", "ReferenceManager"],
    "User": ["ReferenceManager"],
    "Trade": ["TradingService", "PerformanceService"],
    "Position": ["PerformanceService"],
    "DailyPerformance": ["PerformanceService"]
}

# =============================================================================
# Utility Functions
# =============================================================================

def validate_model_relationship(
    source_type: str,
    target_type: str,
    relationship_type: str
) -> bool:
    """
    Validate if a model relationship is allowed.
    Simplified to return whether the target_type is in the specified relationship list.
    """
    return target_type in MODEL_RELATIONSHIPS.get(source_type, {}).get(relationship_type, [])

def validate_service_access(
    source_type: str,
    service_type: str
) -> bool:
    """Validate if a model can access a service type."""
    return service_type in SERVICE_ACCESS.get(source_type, [])

def validate_role_assignment(role: UserRole, assignments: Dict[str, List[str]]) -> bool:
    """
    Validate that the role assignment is compatible.
    - VIEWER should not have any group assignments.
    - EXPORTER should not have any account assignments.
    - ADMIN can have any assignments.
    """
    if role == UserRole.ADMIN:
        return True
    mapping = {
        UserRole.VIEWER: "groups",
        UserRole.EXPORTER: "accounts"
    }
    key = mapping.get(role)
    return not assignments.get(key, [])

# =============================================================================
# Additional Definitions
# =============================================================================

class SettingsType(ABC):
    """
    Marker interface for application settings.
    Extend this class in your settings classes to indicate they are part of the configuration.
    """
    pass

class ConfigValidationError(Exception):
    """
    Exception raised when configuration validation fails.
    """
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"ConfigValidationError: {self.message}"

# =============================================================================
# TYPE_CHECKING Imports
# =============================================================================

if TYPE_CHECKING:
    from app.models.entities.account import Account as AccountType
    from app.models.entities.bot import Bot as BotType  
    from app.models.entities.trade import Trade as TradeType
    from app.models.entities.group import Group as GroupType
    from app.models.entities.user import User as UserType
    from app.models.entities.position_history import PositionHistory as PositionType
    from app.services.trading.service import TradingService as TradingServiceType
    from app.services.websocket.manager import WebSocketManager as WebSocketManagerType
    from app.services.reference.manager import ReferenceManager as ReferenceManagerType
    from app.services.performance.service import PerformanceService as PerformanceServiceType


__all__ = [
    # Configuration
    "Environment",
    "ExchangeType",
    "UserRole",
    "SignalOrderType",
    "TimeFrame",
    "OrderType",
    "TradeSource",
    "TradeStatus",
    "PositionSide",
    "BotStatus",
    "PositionStatus",
    "PerformanceTimeFrame",
    "WebSocketType",
    "ConnectionState",
    "ErrorLevel",
    "ErrorCategory",
    "RecoveryStrategy",
    "LogLevel",
    # Protocols and References
    "LoggerProtocol",
    "DatabaseSessionProtocol",
    "TokenProtocol",
    "TradingServiceProtocol",
    "WebSocketManagerProtocol",
    "ReferenceManagerProtocol",
    "PerformanceServiceProtocol",
    "BaseTokenData",
    "DateRange",
    "BasePosition",
    "BaseTrade",
    "BasePerformanceMetrics",
    "PerformanceMetrics",
    "LogRotation",
    "RateLimits", 
    "ExchangeTimeouts",
    "WebhookConfig",
    "CacheSettings",
    "MonitoringSettings",
    "SettingsType",
    "ConfigValidationError",
    "ModelState",
    "ValidationResult", 
    "TimeRange",
    "PerformanceDict",
    "TimeSeriesData",
    "validate_model_relationship",
    "validate_service_access",
    "validate_role_assignment",
    "MODEL_RELATIONSHIPS",
    "SERVICE_ACCESS",
]
