"""
Central reference point for core module interfaces and type definitions.

Features:
- Service and database protocol definitions 
- Base configuration models and types
- Model relationship definitions
- Core type aliases and constants
"""

from typing import (
    TypeVar, Protocol, Dict, Any, Union, Optional, 
    List, Callable, Type, ForwardRef, Set, TYPE_CHECKING
)
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from pydantic import BaseModel, SecretStr
import logging
from enum import Enum

# ---- Core Enums ----

class Environment(str, Enum):
    """Valid deployment environments."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

class ExchangeType(str, Enum):
    """Supported exchanges."""
    OKX = "okx"
    BYBIT = "bybit"
    BITGET = "bitget"

class UserRole(str, Enum):
    """User permission roles."""
    ADMIN = "admin"
    EXPORTER = "exporter"
    VIEWER = "viewer"

class SignalOrderType(str, Enum):
    """Signal order types for trading operations."""
    LONG_SIGNAL = "Long Signal"
    SHORT_SIGNAL = "Short Signal" 
    LONG_LADDER = "Long Ladder"
    SHORT_LADDER = "Short Ladder"
    POSITION_CONTROL = "Position Control"
    
class TimeFrame(str, Enum):
    """Time frames for bot types."""
    M1 = "1m"
    M5 = "5m" 
    M15 = "15m"
    H1 = "1h"
    H6 = "6h"
    D1 = "1d"
    D3 = "3d"

class OrderType(str, Enum):
    """Trading order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market" 
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"

class TradeSource(str, Enum):
    """Source of trade signals."""
    BOT = "bot"
    TRADING_VIEW = "tradingview"
    TRADING_PANEL = "trading_panel"
    TELEGRAM = "telegram"

class TradeStatus(str, Enum):
    """Trade lifecycle states."""
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ERROR = "error"

class PositionSide(str, Enum):
    """Position direction."""
    LONG = "long"
    SHORT = "short"

class BotStatus(str, Enum):
    """Bot operational states."""
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"

class PositionStatus(str, Enum):
    """Position handling states."""
    INITIALIZED = "initialized"
    OPEN = "open"
    CLOSED = "closed"
    ERROR = "error"

class PerformanceTimeFrame(str, Enum):
    """Time frames for performance aggregation."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

# ---- Websocket Enums ----

class WebSocketType(str, Enum):
    """WebSocket connection type."""
    PUBLIC = "public"
    PRIVATE = "private"

class ConnectionState(Enum):
    """WebSocket connection states."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    RECONNECTING = "reconnecting"
    
# ---- Logging Enums ----

class LogLevel(str, Enum):
    """Logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

# ---- Core Protocols ----

class LoggerProtocol(Protocol):
    """Protocol defining required logging interface."""
    async def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None
    ) -> None: ...
    
    async def log_performance(
        self,
        operation: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None
    ) -> None: ...
    
    def info(self, message: str, **kwargs: Any) -> None: ...
    def warning(self, message: str, **kwargs: Any) -> None: ...
    def error(self, message: str, **kwargs: Any) -> None: ...
    def critical(self, message: str, **kwargs: Any) -> None: ...

class DatabaseSessionProtocol(Protocol):
    """Protocol defining database session operations."""
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def in_transaction(self) -> bool: ...

class TokenProtocol(Protocol):
    """Protocol defining token data and validation."""
    username: str
    exp: datetime  
    role: Optional[str]
    issued_at: datetime
    token_id: str
    
    def validate_expiry(self) -> None: ...

class TradingServiceProtocol(Protocol):
    """Protocol defining trading service interface."""
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
    ) -> Dict[str, Any]: ...

    async def close_position(
        self, 
        symbol: str,
        account_id: str
    ) -> Dict[str, Any]: ...

    async def validate_trade(
        self,
        symbol: str,
        side: str,
        risk_percentage: str,
        leverage: str,
        take_profit: Optional[str] = None
    ) -> Dict[str, bool]: ...

    async def validate_trade_params(
        self,
        symbol: str,
        side: str,
        risk_percentage: float,
        leverage: int,
        take_profit: Optional[str] = None
    ) -> Dict[str, bool]: ...

    async def get_account_balance(
        self,
        account_id: str
    ) -> Dict[str, Union[float, Decimal]]: ...

class WebSocketManagerProtocol(Protocol):
    """Protocol for WebSocket manager interface."""
    async def get_connection(self, identifier: str) -> Any: ...
    async def create_connection(self, config: Dict[str, Any]) -> Any: ...
    async def close_connection(self, identifier: str) -> None: ...
    async def subscribe(self, identifier: str, channel: str) -> None: ...
    async def unsubscribe(self, identifier: str, channel: str) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def get_status(self) -> Dict[str, Any]: ...

class PerformanceServiceProtocol(Protocol):
    """Protocol for performance tracking and calculations."""
    async def update_daily_performance(
        self,
        account_id: str,
        date: datetime,
        metrics: Dict[str, Union[int, float, Decimal]]
    ) -> None: ...

    async def get_account_metrics(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: ...

    async def get_group_metrics(
        self,
        group_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: ...

    async def calculate_statistics(
        self,
        data: List[Dict[str, Any]]
    ) -> Dict[str, Any]: ...


class ReferenceManagerProtocol(Protocol):
    """Protocol defining reference manager interface."""
    async def validate_reference(
        self,
        source_type: str,
        target_type: str,
        reference_id: str
    ) -> bool: ...

    async def get_references(
        self,
        source_type: str,
        reference_id: str
    ) -> List[Dict[str, Any]]: ...

    async def add_reference(
        self,
        source_type: str,
        target_type: str,
        source_id: str,
        target_id: str
    ) -> None: ...

    async def remove_reference(
        self,
        source_type: str,
        target_type: str,
        source_id: str,
        target_id: str
    ) -> None: ...

    async def get_service(
        self,
        service_type: str
    ) -> Any: ...

class PerformanceServiceProtocol(Protocol):
    """Protocol defining performance service interface."""
    async def update_daily_performance(
        self,
        account_id: str,
        date: datetime,
        balance: Decimal,
        equity: Decimal,
        metrics: Dict[str, Union[Decimal, float, int]]
    ) -> None: ...
    
    async def get_account_metrics(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: ...
    
    async def get_group_metrics(
        self,
        group_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]: ...

    async def aggregate_performance(
        self,
        account_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        interval: str = "day"
    ) -> Dict[str, Any]: ...

    async def calculate_statistics(
        self,
        data: List[Dict[str, Any]]
    ) -> Dict[str, Any]: ...

# ---- Base Models ----

class BaseTokenData(BaseModel):
    """Base model for token data."""
    username: str
    exp: datetime
    role: Optional[str] = None
    issued_at: datetime
    token_id: str

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

# ---- Configuration Models ----

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

# ---- Type Variables ----

ModelT = TypeVar("ModelT", bound=BaseModel)
AccountT = TypeVar("AccountT", bound="Account")
BotT = TypeVar("BotT", bound="Bot")
TradeT = TypeVar("TradeT", bound="Trade")
UserT = TypeVar("UserT", bound="User")
GroupT = TypeVar("GroupT", bound="Group")

# ---- Domain Types ----

DateString = str  # YYYY-MM-DD format
TimestampString = str  # ISO format string
Numeric = Union[int, float, Decimal]
ModelState = Dict[str, Any]
ValidationResult = Dict[str, Union[bool, List[str]]]
TimeRange = Dict[str, DateString]
PerformanceDict = Dict[str, Union[Decimal, float, int]]
TimeSeriesData = Dict[datetime, PerformanceDict]
MetricsResponse = Dict[str, Any]

# ---- Service Types ----

# Database
DatabaseConfig = Dict[str, Union[str, int, float]]
DatabaseError = Dict[str, Any]

# Trading
OrderRequest = Dict[str, Any]
OrderResponse = Dict[str, Any]
TradeExecution = Dict[str, Any]
PositionInfo = Dict[str, Any]

# WebSocket 
WebSocketMessage = Dict[str, Any]
WebSocketStatus = Dict[str, Any]
SubscriptionRequest = Dict[str, Any]

# Security
JWTToken = str
PasswordHash = str
TokenMetadata = Dict[str, Union[str, datetime]]
TokenPayload = Dict[str, Any]
LoginAttemptRecord = Dict[str, List[datetime]]

# Logging
LogContext = Dict[str, Any]
LogRecord = Dict[str, Any]
LogHandler = Union[logging.FileHandler, logging.StreamHandler]
LogPath = Union[str, Path]

# ---- Model Relationships ----

MODEL_RELATIONSHIPS = {
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

# ---- Service Access Control ----

SERVICE_ACCESS = {
    "Account": ["TradingService", "WebSocketManager", "PerformanceService"],
    "Bot": ["TradingService", "WebSocketManager", "ReferenceManager"],
    "Group": ["PerformanceService", "ReferenceManager"],
    "User": ["ReferenceManager"],
    "Trade": ["TradingService", "PerformanceService"],
    "Position": ["PerformanceService"],
    "DailyPerformance": ["PerformanceService"]
}

# ---- Custom Exceptions ----

class ConfigValidationError(Exception):
    """Configuration validation error."""
    pass


# ---- Utility Functions ----

def validate_model_relationship(
    source_type: str,
    target_type: str,
    relationship_type: str
) -> bool:
    """Validate if a model relationship is allowed."""
    relationships = MODEL_RELATIONSHIPS.get(source_type, {})
    
    if relationship_type == "has_many":
        return target_type in relationships.get("has_many", [])
        
    elif relationship_type == "belongs_to":
        return target_type in relationships.get("belongs_to", [])
        
    elif relationship_type == "in_groups":
        return target_type in relationships.get("in_groups", [])
        
    return False

def validate_service_access(
    source_type: str,
    service_type: str
) -> bool:
    """Validate if a model can access a service type."""
    if source_type not in SERVICE_ACCESS:
        return False
    return service_type in SERVICE_ACCESS[source_type]

def validate_role_assignment(role: UserRole, assignments: Dict[str, List[str]]) -> bool:
    """Validate role and assignments are compatible."""
    if role == UserRole.VIEWER:
        return not assignments.get("groups", [])
    elif role == UserRole.EXPORTER:
        return not assignments.get("accounts", [])
    elif role == UserRole.ADMIN:
        return True
    return False

# ---- Import Types (for TYPE_CHECKING) ----

if TYPE_CHECKING:
    from app.models.account import Account as AccountType
    from app.models.bot import Bot as BotType  
    from app.models.trade import Trade as TradeType
    from app.models.group import Group as GroupType
    from app.models.user import User as UserType
    from app.models.position_history import PositionHistory as PositionType
    from app.services.trading.service import TradingService as TradingServiceType
    from app.services.websocket.manager import WebSocketManager as WebSocketManagerType
    from app.services.reference.manager import ReferenceManager as ReferenceManagerType
    from app.services.performance.service import PerformanceService as PerformanceServiceType
