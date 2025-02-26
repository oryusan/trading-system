"""
WebSocket service package initialization.

This module provides WebSocket clients for different exchanges and core WebSocket functionality.

Components:
- WebSocket connection types and states
- Base WebSocket client implementation
- Exchange-specific WebSocket clients 
- Centralized connection management
"""

from app.core.references import WebSocketType
from app.services.websocket.base_ws import BaseWebSocket, WebSocketState
from app.services.websocket.okx_ws import OKXWebSocket, OKXConnectionState
from app.services.websocket.bybit_ws import BybitWebSocket, BybitConnectionState
from app.services.websocket.bitget_ws import BitgetWebSocket, BitgetConnectionState
from app.services.websocket.manager import (
    WebSocketManager,
    ConnectionInfo,
    ws_manager
)

__all__ = [
    # Core types
    'WebSocketType',      # WebSocket connection types (public/private)
    'WebSocketState',     # Base connection state tracking
    'ConnectionInfo',     # Connection information and monitoring
    
    # Base implementation
    'BaseWebSocket',      # Base WebSocket client functionality
    
    # Exchange implementations
    'OKXWebSocket',       # OKX exchange WebSocket client
    'OKXConnectionState', # OKX-specific connection state
    'BybitWebSocket',     # Bybit exchange WebSocket client
    'BybitConnectionState', # Bybit-specific connection state
    'BitgetWebSocket',    # Bitget exchange WebSocket client
    'BitgetConnectionState', # Bitget-specific connection state
    
    # Management
    'WebSocketManager',   # WebSocket connection manager class
    'ws_manager'         # Global WebSocket manager instance
]