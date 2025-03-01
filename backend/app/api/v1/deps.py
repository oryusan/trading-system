"""
Enhanced dependency injection system with service integration and error handling.

Features:
- Proper service integration
- Enhanced error propagation (errors are raised so that global exception handlers can process them)
- Performance monitoring
- Access validation
- Reference checking
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

# Core imports
from app.core.config import settings
from app.core.errors.base import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    DatabaseError,
)
from app.core.logging.logger import get_logger
from app.core.references import UserRole, UserContext

logger = get_logger(__name__)

# Initialize OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.app.API_V1_STR}/auth/login", auto_error=False
)

# ---------------------------
# Service Dependencies
# ---------------------------
async def get_service_deps() -> Dict[str, Any]:
    """Return a dictionary of core service dependencies."""
    return {
        "exchange_factory": exchange_factory,
        "symbol_validator": symbol_validator,
        "reference_manager": reference_manager,
        "performance_service": performance_service,
        "ws_manager": ws_manager,
    }

# ---------------------------
# Authentication Helpers
# ---------------------------
async def _authenticate_user(
    request: Request, token: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Validate the token, retrieve the user reference and attach request context.

    Returns a tuple of (user_dict, token_data).
    """
    if not token:
        raise AuthenticationError("Not authenticated", context={"path": request.url.path})

    token_data = await token_manager.validate_token(token)
    user = await reference_manager.get_reference(token_data.get("user_id"), "User")
    if not user:
        raise AuthenticationError("User not found", context={"user_id": token_data.get("user_id")})
    if not user.get("is_active"):
        raise AuthenticationError("Inactive user account", context={"user_id": user.get("id")})

    # Attach request context for tracking
    user["request_context"] = {
        "ip": request.client.host,
        "path": request.url.path,
        "method": request.method,
        "timestamp": datetime.utcnow().isoformat(),
    }
    return user, token_data

async def get_current_user(
    request: Request, token: str = Depends(oauth2_scheme)
) -> Dict[str, Any]:
    """
    Dependency to get the current authenticated user as a dict.
    """
    user, _ = await _authenticate_user(request, token)
    return user

async def get_current_active_user(
    request: Request, token: str = Depends(oauth2_scheme)
) -> UserContext:
    """
    Dependency to get the current active user and convert to a UserContext model.
    """
    user, token_data = await _authenticate_user(request, token)
    # Retrieve permissions (assumed to return a list)
    permissions = await reference_manager.get_user_permissions(str(user.get("id")))
    user_context = UserContext(
        user_id=str(user.get("id")),
        username=user.get("username"),
        role=user.get("role"),
        permissions=permissions,
        token_id=token_data.get("token_id"),
        request_context=user.get("request_context"),
    )
    logger.debug(
        "Validated active user",
        extra={"user_id": str(user.get("id")), "path": request.url.path},
    )
    return user_context

async def get_auth_service() -> Any:
    """
    Dependency to get the initialized authentication service.
    """
    auth_service = AuthenticationService(
        token_manager=token_manager,
        reference_manager=reference_manager,
        password_manager=password_manager,
    )
    return auth_service

# ---------------------------
# User & Admin Dependencies
# ---------------------------
async def get_admin_user(
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Ensure the current user has admin privileges."""
    if current_user.get("role") != UserRole.ADMIN:
        raise AuthorizationError("Admin privileges required", context={"user_id": current_user.get("id")})
    return current_user

# ---------------------------
# Accessible Resources Dependencies
# ---------------------------
async def get_accessible_accounts(
    current_user: Dict = Depends(get_current_user),
) -> List[str]:
    """Retrieve account IDs accessible by the current user."""
    if current_user.get("role") == UserRole.ADMIN:
        accounts = await reference_manager.get_references(
            source_type="User", reference_id=current_user.get("id")
        )
    else:
        accounts = await reference_manager.get_references(
            source_type="User",
            reference_id=current_user.get("id"),
            filter_params={"is_active": True},
        )
    return [str(acc.get("id")) for acc in accounts]

async def get_accessible_bots(current_user: Dict) -> List[str]:
    """Retrieve bot IDs accessible by the current user."""
    if current_user.get("role") == UserRole.ADMIN:
        bots = await reference_manager.get_all_references("Bot")
    else:
        bots = await reference_manager.get_references(
            source_type="User",
            reference_id=current_user.get("id"),
            filter_params={"resource_type": "Bot"},
        )
    return [str(bot.get("id")) for bot in bots]

async def get_accessible_groups(user: Dict) -> List[str]:
    """Retrieve group IDs accessible by the given user."""
    if user.get("role") == UserRole.ADMIN:
        groups = await reference_manager.get_all_references("Group")
    else:
        groups = await reference_manager.get_references(
            source_type="User",
            reference_id=str(user.get("id")),
            filter_params={"resource_type": "Group"},
        )
    return [str(group.get("id")) for group in groups]

# ---------------------------
# Service Dependencies
# ---------------------------
async def get_performance_deps(
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Provide dependencies needed for performance-related operations."""
    deps = await get_service_deps()
    deps["accounts"] = await get_accessible_accounts(current_user)
    deps["bots"] = await get_accessible_bots(current_user)
    return deps

async def get_group_deps(
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Provide dependencies needed for group management."""
    deps = await get_service_deps()
    deps["groups"] = await reference_manager.get_references(
        source_type="User",
        reference_id=str(current_user.get("id")),
        filter_params={"resource_type": "Group"},
    )
    deps["ws_connections"] = await ws_manager.get_active_connections()
    return deps

async def get_trading_deps(
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Provide dependencies needed for trading operations."""
    deps = await get_service_deps()
    deps["accounts"] = await get_accessible_accounts(current_user)
    deps["subscriptions"] = await ws_manager.get_active_subscriptions()
    return deps

async def get_admin_deps(
    current_user: Dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Provide dependencies needed for admin-level operations."""
    deps = await get_service_deps()
    deps["all_accounts"] = await reference_manager.get_all_references("Account")
    deps["all_groups"] = await reference_manager.get_all_references("Group")
    deps["all_bots"] = await reference_manager.get_all_references("Bot")
    return deps

# ---------------------------
# Import Services (at end to avoid circular imports)
# ---------------------------
from app.services.auth.service import AuthenticationService
from app.services.auth.tokens import token_manager
from app.services.auth.password import password_manager
from app.services.exchange.factory import exchange_factory, symbol_validator
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
