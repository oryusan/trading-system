"""
API package initialization for unified access to API routers and dependencies.
"""

from fastapi import APIRouter
from app.api.v1.api import api_router
from .v1.deps import (
    get_current_user,
    get_current_active_user,
    get_admin_user,
    get_service_deps,
    get_performance_deps,
    get_trading_deps,
    get_admin_deps
)

__all__ = [
    'api_router',
    'get_current_user',
    'get_current_active_user',
    'get_admin_user',
    'get_service_deps',
    'get_performance_deps',
    'get_trading_deps',
    'get_admin_deps'
]