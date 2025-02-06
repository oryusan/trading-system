"""
System-wide constants and configuration values.

Features:
- Trading constants
- System limits
- Default values
- Timeouts
- Cache durations
- Rate limits
- Precision settings
"""

from typing import Dict, Union, Any
from decimal import Decimal

# Base Types
DateString = str  # Format: YYYY-MM-DD
Numeric = Union[int, float, Decimal]

# Trading Constants
class TradingConstants:
    """Trading system constants."""
    
    # Leverage limits
    MIN_LEVERAGE: int = 1
    MAX_LEVERAGE: int = 100
    
    # Risk limits (percentage)
    MIN_RISK_PERCENTAGE: float = 0.01  # 0.01%
    MAX_RISK_PERCENTAGE: float = 6.00  # 6%
    
    # Position defaults
    DEFAULT_TAKE_PROFIT: int = 0  # 0 = no TP
    DEFAULT_STOP_LOSS: int = 0    # 0 = no SL
    
    # Timeouts (seconds)
    ORDER_TIMEOUT: int = 60
    POSITION_SYNC_INTERVAL: int = 300
    PRICE_SYNC_INTERVAL: int = 10
    
    # Cache durations (seconds)
    SYMBOL_CACHE_TTL: int = 86400      # 1 day
    EXCHANGE_INFO_TTL: int = 3600      # 1 hour
    MARKET_DATA_TTL: int = 60          # 1 minute
    
    # Rate limits (requests per minute)
    PUBLIC_RATE_LIMIT: int = 100
    PRIVATE_RATE_LIMIT: int = 30
    
    # Precision settings
    PNL_PRECISION: int = 8
    FEE_PRECISION: int = 8
    PRICE_PRECISION: int = 8

    @classmethod
    def as_dict(cls) -> Dict[str, Any]:
        """Get constants as dictionary."""
        return {
            key: value for key, value in vars(cls).items() 
            if not key.startswith("_")
        }

# System Constants
class SystemConstants:
    """System-wide configuration constants."""
    
    # API Settings
    MAX_PAGE_SIZE: int = 1000
    DEFAULT_PAGE_SIZE: int = 50
    
    # WebSocket Settings
    WS_PING_INTERVAL: int = 30
    WS_RECONNECT_DELAY: int = 5
    WS_MAX_RECONNECTS: int = 5
    
    # Cache Settings
    CACHE_KEY_PREFIX: str = "trading_app:"
    CACHE_DEFAULT_TTL: int = 300
    
    # Performance Settings
    BATCH_SIZE: int = 500
    MAX_WORKERS: int = 4
    
    # Monitoring Settings
    HEALTH_CHECK_INTERVAL: int = 60
    METRICS_INTERVAL: int = 30

# Export constants
trading_constants = TradingConstants.as_dict()
system_constants = {
    key: value for key, value in vars(SystemConstants).items() 
    if not key.startswith("_")
}

# Type Exports
__all__ = [
    "DateString",
    "Numeric",
    "TradingConstants",
    "SystemConstants",
    "trading_constants",
    "system_constants"
]