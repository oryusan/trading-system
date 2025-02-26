"""
Version 1 API package initialization with router registration.
"""

from fastapi import APIRouter

# Local imports
from app.api.v1.api import api_router as v1_api_router
from app.api.v1.deps import (
    get_current_user,
    get_current_active_user,
    get_admin_user,
    get_service_deps,
    get_performance_deps,
    get_trading_deps,
    get_admin_deps,
)

# Initialize the main API router and include version 1 routes.
router = APIRouter()
router.include_router(v1_api_router, prefix="/v1")

# Group dependency functions to reduce redundancy in __all__
DEPENDENCY_FUNCTIONS = [
    get_current_user,
    get_current_active_user,
    get_admin_user,
    get_service_deps,
    get_performance_deps,
    get_trading_deps,
    get_admin_deps,
]

# Export the router and dependency functions.
__all__ = ["router"] + [func.__name__ for func in DEPENDENCY_FUNCTIONS]
