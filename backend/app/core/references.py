"""
Central reference point for core module interfaces and type definitions.

This file includes:
- Protocol definitions for logging, database sessions, tokens, trading services, WebSocket managers, 
  performance services, and reference managers.
- Base models for tokens, positions, trades, performance metrics, etc.
- Configuration models for logging rotation, rate limits, exchange timeouts, and more.
- Domain and service type aliases.
- Utility functions for model relationships and service access control.
- TYPE_CHECKING imports.

Optimizations:
- Reduced import overhead
- Consolidated type definitions
- Improved Protocol definitions for better type checking
- Added performance-optimized interfaces
"""

# Standard Library Imports
from abc import ABC
from datetime import datetime, timedelta
from decimal import Decimal
from typing import (
    Any, Callable, Dict, List, Optional, Set, Type, TypeVar, Union, 
    Protocol, NamedTuple, Literal, Awaitable, cast, overload, 
    runtime_checkable, TYPE_CHECKING
)
from functools import lru_cache

# Third-Party Imports
from pydantic import BaseModel, SecretStr, Field, RootModel

# Lazy imports for circular dependency prevention
if TYPE_CHECKING:
    # Local Imports
    from app.core.enums import (
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
        BotType,
        PositionStatus,
        PerformanceTimeFrame,
        WebSocketType,
        ConnectionState,
        ErrorLevel,
        ErrorCategory,
        RecoveryStrategy,
        LogLevel,
    )
else:
    # Minimal imports for runtime
    from app.core.enums import (
        UserRole,
        OrderType,
        TradeSource,
        PositionSide,
    )

# =============================================================================
# Type Variables
# =============================================================================

T = TypeVar('T')
TService = TypeVar('TService')
TModel = TypeVar('TModel', bound=BaseModel)
TDict = TypeVar('TDict', bound=Dict[str, Any])

# =============================================================================
# Protocol Definitions
# =============================================================================

@runtime_checkable
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


@runtime_checkable
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
    async def get_collection(self, name: str) -> Any:
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
    
    def is_expired(self) -> bool:
        ...
    
    def get_remaining_time(self) -> timedelta:
        ...


@runtime_checkable
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
    
    # Connection management
    async def is_connected(self) -> bool:
        ...
    
    async def connect(self) -> bool:
        ...
    
    async def disconnect(self) -> bool:
        ...


@runtime_checkable
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
    async def get_connection_stats(self) -> Dict[str, int]:
        ...


@runtime_checkable
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
    
    async def batch_update(
        self,
        updates: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        ...


@runtime_checkable
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
    
    async def validate_access(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
        access_type: str = "read"
    ) -> bool:
        ...


# =============================================================================
# Cache Protocol
# =============================================================================

@runtime_checkable
class CacheProtocol(Protocol):
    """Interface for cache operations."""
    async def get(self, key: str) -> Any:
        ...
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        ttl: Optional[int] = None
    ) -> bool:
        ...
    
    async def delete(self, key: str) -> bool:
        ...
    
    async def exists(self, key: str) -> bool:
        ...
    
    async def clear(self, pattern: Optional[str] = None) -> int:
        ...
    
    async def increment(self, key: str, amount: int = 1) -> int:
        ...
    
    async def expire(self, key: str, ttl: int) -> bool:
        ...
    
    async def get_many(self, keys: List[str]) -> Dict[str, Any]:
        ...
    
    async def set_many(
        self, 
        items: Dict[str, Any], 
        ttl: Optional[int] = None
    ) -> bool:
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
    
    def is_expired(self) -> bool:
        """Check if the token is expired."""
        return datetime.utcnow() > self.exp
    
    def get_remaining_time(self) -> timedelta:
        """Get the remaining time until token expiration."""
        now = datetime.utcnow()
        if now > self.exp:
            return timedelta(0)
        return self.exp - now


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
    
    def has_permission(self, permission: str) -> bool:
        """Check if the user has a specific permission."""
        return permission in self.permissions
    
    def is_admin(self) -> bool:
        """Check if the user has admin role."""
        return self.role == "admin"
    
    def get_request_path(self) -> Optional[str]:
        """Get the request path from the context."""
        if not self.request_context:
            return None
        return self.request_context.get("path")
    
    def get_client_ip(self) -> Optional[str]:
        """Get the client IP from the context."""
        if not self.request_context:
            return None
        return self.request_context.get("client_ip")


class DateRange(BaseModel):
    """Date range for queries."""
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "start_date": "2023-01-01",
                    "end_date": "2023-01-31"
                }
            ]
        }
    }
    
    def to_datetime(self) -> Dict[str, datetime]:
        """Convert string dates to datetime objects."""
        from datetime import datetime
        return {
            "start_date": datetime.strptime(self.start_date, "%Y-%m-%d"),
            "end_date": datetime.strptime(self.end_date, "%Y-%m-%d")
        }


class BasePosition(BaseModel):
    """Base model for position data."""
    symbol: str
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    leverage: int
    take_profit: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    
    def calculate_value(self) -> Decimal:
        """Calculate the position value."""
        return self.size * self.entry_price
    
    def calculate_liquidation_price(self) -> Optional[Decimal]:
        """Calculate estimated liquidation price."""
        if self.leverage <= 0:
            return None
            
        # This is a simplified calculation
        margin = self.calculate_value() / self.leverage
        if self.side == PositionSide.LONG:
            return self.entry_price * (1 - 1 / self.leverage)
        else:
            return self.entry_price * (1 + 1 / self.leverage)
            
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side.value if hasattr(self.side, "value") else self.side,
            "size": str(self.size),
            "entry_price": str(self.entry_price),
            "leverage": self.leverage,
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "stop_loss": str(self.stop_loss) if self.stop_loss else None,
        }


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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type.value if hasattr(self.order_type, "value") else self.order_type,
            "size": str(self.size),
            "leverage": self.leverage,
            "risk_percentage": str(self.risk_percentage),
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "source": self.source.value if hasattr(self.source, "value") else self.source,
        }


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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with string representation of decimals."""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "total_volume": str(self.total_volume),
            "total_pnl": str(self.total_pnl),
            "trading_fees": str(self.trading_fees),
            "funding_fees": str(self.funding_fees),
            "net_pnl": str(self.net_pnl),
            "win_rate": self.win_rate,
            "roi": self.roi,
            "drawdown": self.drawdown,
        }


class PerformanceMetrics(BasePerformanceMetrics):
    """Performance calculation results."""
    start_balance: Decimal
    end_balance: Decimal
    
    model_config = {
        "arbitrary_types_allowed": True
    }
    
    def calculate_profit_factor(self) -> float:
        """Calculate profit factor (gross profit / gross loss)."""
        winning_amount = self.total_pnl if self.total_pnl > 0 else Decimal(0)
        losing_amount = -self.total_pnl if self.total_pnl < 0 else Decimal(0)
        
        if losing_amount == 0:
            return float('inf') if winning_amount > 0 else 0.0
            
        return float(winning_amount / losing_amount)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the metrics."""
        return {
            "balance_change": str(self.end_balance - self.start_balance),
            "balance_change_percent": float(((self.end_balance / self.start_balance) - 1) * 100) if self.start_balance else 0,
            "win_rate": self.win_rate,
            "roi": self.roi,
            "drawdown": self.drawdown,
            "profit_factor": self.calculate_profit_factor(),
        }


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
    connection_pool_size: int = 20
    connection_keep_alive: bool = True


class WebhookConfig(BaseModel):
    """Webhook configuration."""
    tradingview_secret: SecretStr
    forward_url: Optional[str] = None
    timeout_seconds: int = 30
    max_payload_size: int = 1048576  # 1MB
    enable_request_validation: bool = True


class CacheSettings(BaseModel):
    """Cache configuration."""
    redis_url: str
    default_ttl: int = 300
    symbol_info_ttl: int = 3600
    use_compression: bool = False
    batch_size: int = 100


class MonitoringSettings(BaseModel):
    """Monitoring configuration."""
    enable_metrics: bool = True
    metrics_port: int = 9090
    health_check_interval: int = 60
    trace_requests: bool = False
    enable_profiling: bool = False


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
QueryFilter = Dict[str, Any]
UpdateOperation = Dict[str, Any]
DatabaseResult = Dict[str, Any]

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
PagedResult = Dict[str, Any]


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
    "Group": ["TradingService", "PerformanceService", "ReferenceManager"],
    "User": ["ReferenceManager"],
    "Trade": ["TradingService", "PerformanceService"],
    "Position": ["PerformanceService"],
    "DailyPerformance": ["PerformanceService"]
}

# =============================================================================
# Utility Functions
# =============================================================================

@lru_cache(maxsize=1000)
def validate_model_relationship(
    source_type: str,
    target_type: str,
    relationship_type: str
) -> bool:
    """
    Validate if a model relationship is allowed.
    Used LRU cache for performance.
    
    Args:
        source_type: The source model type
        target_type: The target model type
        relationship_type: The type of relationship to validate
        
    Returns:
        True if the relationship is valid, False otherwise
    """
    return target_type in MODEL_RELATIONSHIPS.get(source_type, {}).get(relationship_type, [])


@lru_cache(maxsize=1000)
def validate_service_access(
    source_type: str,
    service_type: str
) -> bool:
    """
    Validate if a model can access a service type.
    Used LRU cache for performance.
    
    Args:
        source_type: The source model type
        service_type: The service type to access
        
    Returns:
        True if access is allowed, False otherwise
    """
    return service_type in SERVICE_ACCESS.get(source_type, [])


def validate_role_assignment(role: UserRole, assignments: Dict[str, List[str]]) -> bool:
    """
    Validate that the role assignment is compatible.
    - VIEWER should not have any group assignments.
    - EXPORTER should not have any account assignments.
    - ADMIN can have any assignments.
    
    Args:
        role: The user role
        assignments: Dictionary of assignments
        
    Returns:
        True if the assignments are valid for the role, False otherwise
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
# Access Control Utilities
# =============================================================================

class AccessControl:
    """Utilities for access control and permission checking."""
    
    @staticmethod
    def check_permission(
        user: UserContext, 
        permission: str, 
        resource_type: Optional[str] = None
    ) -> bool:
        """
        Check if a user has a specific permission.
        
        Args:
            user: The user context
            permission: The permission to check
            resource_type: Optional resource type to scope the permission
            
        Returns:
            True if the user has the permission, False otherwise
        """
        # Admins have all permissions
        if user.is_admin():
            return True
            
        # Check for explicit permission
        if permission in user.permissions:
            return True
            
        # Check for scoped permission if resource type provided
        if resource_type:
            scoped_perm = f"{resource_type}:{permission}"
            return scoped_perm in user.permissions
            
        return False
    
    @staticmethod
    def get_accessible_resources(
        user: UserContext,
        resource_type: str
    ) -> List[str]:
        """
        Get IDs of resources that a user can access.
        
        Args:
            user: The user context
            resource_type: The type of resource
            
        Returns:
            List of resource IDs
        """
        # Extract resource IDs from permissions
        resource_prefix = f"{resource_type}:access:"
        resource_ids = []
        
        for perm in user.permissions:
            if perm.startswith(resource_prefix):
                resource_id = perm[len(resource_prefix):]
                resource_ids.append(resource_id)
                
        return resource_ids


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
# Additional Domain Types
# =============================================================================

class PageOptions(BaseModel):
    """Pagination options for API queries."""
    page: int = Field(1, ge=1, description="Page number")
    page_size: int = Field(50, ge=1, le=1000, description="Items per page")
    sort_by: Optional[str] = Field(None, description="Field to sort by")
    sort_order: Optional[str] = Field(None, description="Sort order (asc/desc)")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for query parameters."""
        result = {"page": self.page, "page_size": self.page_size}
        if self.sort_by:
            result["sort_by"] = self.sort_by
        if self.sort_order:
            result["sort_order"] = self.sort_order
        return result
    
    def get_skip(self) -> int:
        """Calculate the number of items to skip."""
        return (self.page - 1) * self.page_size
    
    def get_limit(self) -> int:
        """Get the page size as limit."""
        return self.page_size


class PagedResponse(RootModel):
    """Standardized paged response format."""
    root: Dict[str, Any]
    
    def __init__(self, 
                items: List[Any], 
                total: int, 
                page: int = 1, 
                page_size: int = 50,
                **kwargs: Any):
        """
        Initialize a paged response.
        
        Args:
            items: The items for the current page
            total: Total number of items
            page: Current page number
            page_size: Items per page
            **kwargs: Additional metadata
        """
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        
        data = {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": total_pages
            }
        }
        
        # Add any additional metadata
        if kwargs:
            data["meta"] = kwargs
            
        super().__init__(root=data)


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
    from app.services.websocket.manager import WebSocketManager as WebSocketManagerType
    from app.services.reference.manager import ReferenceManager as ReferenceManagerType
    from app.services.performance.service import PerformanceService as PerformanceServiceType


__all__ = [
    # Protocols
    "LoggerProtocol",
    "DatabaseSessionProtocol",
    "TokenProtocol",
    "TradingServiceProtocol",
    "WebSocketManagerProtocol",
    "ReferenceManagerProtocol",
    "PerformanceServiceProtocol",
    "CacheProtocol",
    # Base Models
    "BaseTokenData",
    "UserContext",
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
    # Type Aliases
    "JWTToken",
    "PasswordHash",
    "TokenMetadata",
    "TokenPayload",
    "LoginAttemptRecord",
    "DatabaseConfig",
    "DatabaseError",
    "QueryFilter",
    "UpdateOperation",
    "DatabaseResult",
    "OrderRequest",
    "OrderResponse",
    "TradeExecution",
    "PositionInfo",
    "ModelState",
    "ValidationResult", 
    "TimeRange",
    "PerformanceDict",
    "TimeSeriesData",
    "PagedResult",
    # Utility Functions
    "validate_model_relationship",
    "validate_service_access",
    "validate_role_assignment",
    # Constants
    "MODEL_RELATIONSHIPS",
    "SERVICE_ACCESS",
    # Additional Domain Types
    "PageOptions",
    "PagedResponse",
    "AccessControl",
    # Type Variables
    "T",
    "TService",
    "TModel",
    "TDict",
]