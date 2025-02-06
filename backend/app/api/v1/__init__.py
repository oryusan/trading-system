"""
V1 API package initialization with router registration.
"""

from fastapi import APIRouter

# Import routers from API versions
from app.api.v1.api import api_router as api_v1_router
from app.api.v1.deps import (
    get_current_user,
    get_current_active_user,
    get_admin_user,
    get_service_deps,
    get_performance_deps,
    get_trading_deps,
    get_admin_deps
)

# Create main router that includes versioned routers
root_router = APIRouter()
root_router.include_router(api_v1_router, prefix="/v1")

__all__ = [
    'root_router',
    'get_current_user',
    'get_current_active_user', 
    'get_admin_user',
    'get_service_deps',
    'get_performance_deps',
    'get_trading_deps',
    'get_admin_deps'
]