"""
Core model definitions with enhanced error handling and service integration.

Models:

Core Models:
- User: Role-based access control, authentication, and resource assignments
- Bot: Signal routing, WebSocket integration, and performance tracking
- Account: Exchange API management, state validation, and balance tracking
- AccountGroup: Multi-group management, performance tracking, and WebSocket status
- Trade: Trade lifecycle management, performance metrics, and state tracking

Market Data Models:
- SymbolData: Unified symbol mapping and specifications with validation and verification

Performance Models:
- PositionHistory: Historical position tracking, P&L calculation, and performance metrics
- DailyPerformance: Daily performance aggregation, balance tracking, and risk metrics

Features across all models:
- Enhanced error handling with rich context
- Service layer integration (reference manager, performance tracking)
- WebSocket connection management where applicable
- Comprehensive validation and state tracking
- Proper logging and monitoring
"""

from typing import TYPE_CHECKING

# Core models
from app.models.entities.user import User
from app.models.entities.bot import Bot
from app.models.entities.account import Account
from app.models.entities.trade import Trade

if TYPE_CHECKING:
    from app.models.entities.group import AccountGroup
else:
    AccountGroup = None

# Market data models
from app.models.entities.symbol_data import SymbolData

# Performance tracking models
from app.models.entities.position_history import PositionHistory
from app.models.entities.daily_performance import DailyPerformance

# Core reference types
from app.core.references import (
    # Exchange types
    ExchangeType,
    
    # User roles and permissions
    UserRole,
    
    # Trading enums
    TimeFrame,
    OrderType,
    TradeSource,
    TradeStatus,
    BotStatus,
    
    # Position management
    PositionSide,
    PositionStatus
)

__all__ = [
    # Core models
    "User",
    "Bot", 
    "Account",
    "AccountGroup",
    "Trade",
    
    # Market data models
    "SymbolData",
    
    # Performance models
    "PositionHistory",
    "DailyPerformance",
    
    # Exchange types
    "ExchangeType",
    
    # User types
    "UserRole",
    
    # Trading types
    "BotStatus", 
    "TimeFrame",
    "OrderType",
    "TradeSource",
    "TradeStatus",
    
    # Position types
    "PositionSide",
    "PositionStatus"
]