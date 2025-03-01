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

class BotType(str, Enum):
    """Bot types for trading operations."""
    AUTOMATED = "automated"
    MANUAL = "manual" 

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

# ---- Error Enums ----

class ErrorLevel(str, Enum):
    """Error severity levels for notification and handling."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(str, Enum):
    """Error categories for classification and routing."""
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATABASE = "database"
    EXCHANGE = "exchange"
    NETWORK = "network"
    WEBSOCKET = "websocket"
    RATELIMIT = "ratelimit"
    SYSTEM = "system"

# ---- Recovery Enums ----

class RecoveryStrategy(str, Enum):
    """Available error recovery strategies."""
    RETRY = "retry"
    WAIT_AND_RETRY = "wait_and_retry"
    RECONNECT = "reconnect"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    CANCEL_AND_RETRY = "cancel_and_retry"
    CLOSE_AND_RESET = "close_and_reset"

# ---- Logging Enums ----

class LogLevel(str, Enum):
    """Logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
