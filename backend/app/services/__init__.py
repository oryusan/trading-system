"""
Services package initialization.

This module aggregates the core services used across the trading system:
- Authentication and security services
- Exchange operations and symbol management
- WebSocket connections and real-time data
- Performance tracking and metrics
- System monitoring and automation
- Telegram notifications and commands

Each service group provides specific functionality while maintaining separation of concerns
and proper dependency management.
"""

# Authentication and Security Services
from app.services.auth.service import auth_service
from app.services.auth.password import password_manager
from app.services.auth.tokens import token_manager
from app.services.auth.tracking import login_tracker

# Exchange Operations and Symbol Management
from app.services.exchange.factory import (
    exchange_factory,  # Factory for creating exchange instances
    symbol_validator   # Symbol validation and normalization
)
from app.services.exchange.operations import ExchangeOperations

# WebSocket Base Types
from app.services.websocket.base_ws import (
    BaseWebSocket,    # Base WebSocket implementation
    WebSocketState    # Connection state tracking
)

# WebSocket Connection Management
from app.services.websocket.manager import (
    WebSocketManager,  # WebSocket connection manager class
    ConnectionInfo,    # Connection information container
    ws_manager        # Global WebSocket manager instance
)

# Exchange-Specific WebSocket Implementations
from app.services.websocket.okx_ws import (
    OKXWebSocket,
    OKXConnectionState
)
from app.services.websocket.bybit_ws import (
    BybitWebSocket,
    BybitConnectionState
)
from app.services.websocket.bitget_ws import (
    BitgetWebSocket,
    BitgetConnectionState
)

# Performance Tracking Services
from app.services.performance.service import performance_service
from app.services.performance.calculator import PerformanceCalculator
from app.services.performance.aggregator import PerformanceAggregator
from app.services.performance.storage import PerformanceStorage

# Reference Management
from app.services.reference.manager import (
    ReferenceManager,    # Reference management class
    reference_manager    # Global reference manager instance
)

# System Monitoring and Management
from app.services.bot_monitor import bot_monitor
from app.services.cron_jobs import cron_service

# Telegram Integration
from app.services.telegram.service import telegram_bot
from app.services.telegram.handlers import (
    start_command,
    status_command,
    balance_command,
    performance_command,
    help_command,
    register_handlers
)

# Core Types
from app.core.references import (
    WebSocketType,      # WebSocket connection types
    ConnectionState,    # Connection state enumeration
)

__all__ = [
    # Authentication Services
    "auth_service",
    "password_manager", 
    "token_manager",
    "login_tracker",

    # Exchange Services
    "exchange_factory",
    "symbol_validator",
    "ExchangeOperations",

    # WebSocket Base
    "BaseWebSocket",
    "WebSocketState",
    "WebSocketType",
    "ConnectionState",
    "ConnectionInfo",

    # WebSocket Management
    "WebSocketManager",
    "ws_manager",

    # Exchange WebSocket Implementations
    "OKXWebSocket",
    "OKXConnectionState",
    "BybitWebSocket",
    "BybitConnectionState",
    "BitgetWebSocket",
    "BitgetConnectionState",

    # Performance Services
    "performance_service",
    "PerformanceCalculator",
    "PerformanceAggregator", 
    "PerformanceStorage",

    # Reference Management
    "ReferenceManager",
    "reference_manager",

    # System Services
    "bot_monitor",
    "cron_service",

    # Telegram Integration
    "telegram_bot",
    "start_command",
    "status_command", 
    "balance_command",
    "performance_command",
    "help_command",
    "register_handlers"
]