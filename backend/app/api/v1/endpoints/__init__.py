"""
This module imports and re-exports all version 1 (v1) endpoint routers.

Routers:
- auth_router: Handles authentication and token management endpoints.
- trading_router: Provides trading-related endpoints (execute trades, view trades, close positions, etc.).
- bots_router: Manages bot creation, status updates, and account connections.
- accounts_router: Handles account creation, listing, balance updates, group assignments, and performance checks.
- groups_router: Manages account groups (create, list, performance metrics, export trade history).
- webhook_router: Processes incoming webhooks (e.g., from TradingView) for executing trades.
- ws_router: Handles WebSocket connections for real-time UI communication.

By listing these routers in `__all__`, we provide a straightforward way for the main API router to include all v1 endpoints.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.trading import router as trading_router
from app.api.v1.endpoints.bots import router as bots_router
from app.api.v1.endpoints.accounts import router as accounts_router
from app.api.v1.endpoints.groups import router as groups_router
from app.api.v1.endpoints.webhook import router as webhook_router
from app.api.v1.endpoints.ws import router as ws_router

__all__ = (
    'auth_router',
    'trading_router', 
    'bots_router',
    'accounts_router',
    'groups_router',
    'webhook_router',
    'ws_router'
)