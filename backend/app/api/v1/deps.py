"""
Enhanced dependency injection system with service integration and error handling.

Features:
- Proper service integration
- Enhanced error handling
- Performance monitoring
- Access validation
- Reference checking
"""

from typing import AsyncGenerator, Optional, List, Dict, Any
from fastapi import Depends, Request, HTTPException
from fastapi.security import OAuth2PasswordBearer
from datetime import datetime, timedelta
from decimal import Decimal

# Core imports
from app.core.config import settings
from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    DatabaseError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import (
    UserRole,
    TimeRange,
    ValidationResult
)

logger = get_logger(__name__)

# Initialize OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    auto_error=False
)

# Service initialization
async def get_service_deps() -> Dict[str, Any]:
    """Get initialized service dependencies."""
    return {
        "exchange_factory": exchange_factory,
        "symbol_validator": symbol_validator,
        "reference_manager": reference_manager,
        "performance_service": performance_service,
        "ws_manager": ws_manager
    }

# Authentication dependencies
async def get_auth_service() -> AuthenticationService:
    """Get initialized authentication service with dependencies."""
    try:
        auth_service = AuthenticationService(
            token_manager=token_manager,
            reference_manager=reference_manager,
            password_manager=password_manager
        )
        return auth_service
        
    except Exception as e:
        await handle_api_error(
            error=e,
            context={"service": "auth"},
            log_message="Failed to initialize auth service"
        )

async def get_current_active_user(
    request: Request,
    token: str = Depends(oauth2_scheme)
) -> UserContext:
    """Get and validate current active user."""
    try:
        if not token:
            raise AuthenticationError(
                "Not authenticated",
                context={"path": request.url.path}
            )

        # Validate token
        token_data = await token_manager.validate_token(token)
        
        # Get user
        user = await reference_manager.get_reference(
            token_data.get("user_id"),
            "User"
        )
        if not user:
            raise AuthenticationError(
                "User not found",
                context={"user_id": token_data.get("user_id")}
            )

        # Check if user is active
        if not user.get("is_active"):
            raise AuthenticationError(
                "Inactive user account",
                context={"user_id": user.get("id")}
            )
            
        # Create user context
        user_context = UserContext(
            user_id=str(user.get("id")),
            username=user.get("username"),
            role=user.get("role"),
            permissions=await reference_manager.get_user_permissions(
                str(user.get("id"))
            ),
            token_id=token_data.get("token_id")
        )

        # Add request context for tracking
        user_context.request_context = {
            "ip": request.client.host,
            "path": request.url.path,
            "method": request.method,
            "timestamp": datetime.utcnow().isoformat()
        }

        logger.debug(
            "Validated active user",
            extra={
                "user_id": str(user.get("id")),
                "path": request.url.path
            }
        )

        return user_context

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "path": request.url.path,
                "method": request.method
            },
            log_message="Authentication failed"
        )

# User Dependencies
async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme)
) -> Dict[str, Any]:
    """Get current authenticated user with validation."""
    try:
        if not token:
            raise AuthenticationError(
                "Not authenticated",
                context={"path": request.url.path}
            )

        # Validate token
        token_data = await token_manager.validate_token(token)

        # Get user
        user = await reference_manager.get_reference(
            token_data.get("user_id"),
            "User"
        )
        if not user:
            raise AuthenticationError(
                "User not found",
                context={"user_id": token_data.get("user_id")}
            )

        # Check if user is active
        if not user.get("is_active"):
            raise AuthenticationError(
                "Inactive user account",
                context={"user_id": user.get("id")}
            )

        # Add request context
        user["request_context"] = {
            "ip": request.client.host,
            "path": request.url.path,
            "method": request.method,
            "timestamp": datetime.utcnow().isoformat()
        }

        return user

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "path": request.url.path,
                "method": request.method
            },
            log_message="Authentication failed"
        )
        raise

async def get_admin_user(
    current_user: Dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Verify admin privileges."""
    if current_user.get("role") != UserRole.ADMIN:
        raise AuthorizationError(
            "Admin privileges required",
            context={"user_id": current_user.get("id")}
        )
    return current_user

# Access Control Dependencies
async def get_accessible_accounts(
    current_user: Dict = Depends(get_current_user)
) -> List[str]:
    """Get accounts accessible to user."""
    try:
        accounts = []
        
        # Admins can access all accounts
        if current_user.get("role") == UserRole.ADMIN:
            accounts = await reference_manager.get_references(
                source_type="User",
                reference_id=current_user.get("id")
            )
        else:
            # Get assigned accounts
            accounts = await reference_manager.get_references(
                source_type="User",
                reference_id=current_user.get("id"),
                filter_params={"is_active": True}
            )

        return [str(acc.get("id")) for acc in accounts]

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": current_user.get("id")},
            log_message="Failed to get accessible accounts"
        )
        raise

async def get_accessible_groups(user: Dict) -> List[str]:
    """Get groups accessible to user."""
    try:
        if user.get("role") == UserRole.ADMIN:
            groups = await reference_manager.get_all_references("Group")
        else:
            groups = await reference_manager.get_references(
                source_type="User",
                reference_id=str(user.get("id")),
                filter_params={"resource_type": "Group"}
            )
        return [str(group.get("id")) for group in groups]
    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": str(user.get("id"))},
            log_message="Failed to get accessible groups"
        )

# Service Dependencies  
async def get_performance_deps(
    current_user: Dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get performance service dependencies."""
    try:
        deps = await get_service_deps()
        
        # Add accessible resources
        deps["accounts"] = await get_accessible_accounts(current_user)
        deps["bots"] = await get_accessible_bots(current_user)

        return deps

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": current_user.get("id")},
            log_message="Failed to get performance dependencies"
        )
        raise

# Group-specific Dependencies 
async def get_group_deps(
    current_user: Dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get group management dependencies."""
    try:
        deps = await get_service_deps()

        # Add accessible groups
        deps["groups"] = await reference_manager.get_references(
            source_type="User",
            reference_id=str(current_user.get("id")),
            filter_params={"resource_type": "Group"}
        )

        # Add WebSocket connection info
        deps["ws_connections"] = await ws_manager.get_active_connections()

        return deps

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": str(current_user.get("id"))},
            log_message="Failed to get group dependencies"
        )

# Trading Dependencies
async def get_trading_deps(
    current_user: Dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get trading operation dependencies."""
    try:
        deps = await get_service_deps()
        
        # Add account access
        deps["accounts"] = await get_accessible_accounts(current_user)
        
        # Add WebSocket subscriptions
        deps["subscriptions"] = await ws_manager.get_active_subscriptions()

        return deps

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": str(current_user.get("id"))},
            log_message="Failed to get trading dependencies"
        )

# Admin Dependencies
async def get_admin_deps(
    current_user: Dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Get admin operation dependencies."""
    try:
        deps = await get_service_deps()
        
        # Add full resource access
        deps["all_accounts"] = await reference_manager.get_all_references("Account")
        deps["all_groups"] = await reference_manager.get_all_references("Group") 
        deps["all_bots"] = await reference_manager.get_all_references("Bot")

        return deps

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"user_id": str(current_user.get("id"))},
            log_message="Failed to get admin dependencies"
        )

# Import services at end to avoid circular imports
from app.services.auth.service import AuthenticationService
from app.services.auth.tokens import token_manager  
from app.services.auth.password import password_manager
from app.services.exchange.factory import exchange_factory, symbol_validator
from app.services.websocket.manager import ws_manager
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.auth.tokens import token_manager