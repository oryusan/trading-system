"""
Authentication endpoints with enhanced security and error handling.

Features:
- JWT token management with refresh flows
- Session tracking and validation  
- Device fingerprinting and monitoring
- Rate limiting and brute force protection
- Comprehensive audit logging
- Password reset functionality 
- Permission management
"""

from fastapi import APIRouter, Depends, Request, status
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    DatabaseError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import UserRole, ValidationResult

from app.api.v1.references import (
    LoginRequest,
    TokenResponse,
    UserContext,
    DeviceInfo,
    ServiceResponse
)

router = APIRouter()
logger = get_logger(__name__)

@router.post("/login", response_model=TokenResponse) 
async def login(
    request: Request,
    login_data: LoginRequest,
    auth_service = Depends(get_auth_service)
) -> TokenResponse:
    """Authenticate user and create session with enhanced security."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.client.host,
        "user_agent": request.headers.get("User-Agent"),
        "username": login_data.username,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Add device tracking info
        if not login_data.device_info:
            login_data.device_info = DeviceInfo(
                ip_address=request.client.host,
                user_agent=request.headers.get("User-Agent", "unknown"),
                device_type=request.headers.get("Sec-CH-UA-Mobile", "unknown")
            )

        # Authenticate via service
        auth_result = await auth_service.authenticate_user(
            username=login_data.username,
            password=login_data.password,
            device_info=login_data.device_info,
            context=context
        )

        # Update metrics
        await performance_service.update_auth_metrics(
            user_id=str(auth_result.user.id),
            metrics={
                "last_login": datetime.utcnow(),
                "login_success": True,
                "device_info": login_data.device_info.dict()
            }
        )

        logger.info(
            "User login successful",
            extra={
                **context,
                "user_id": str(auth_result.user.id)
            }
        )

        return TokenResponse(
            access_token=auth_result.session.access_token,
            refresh_token=auth_result.session.refresh_token,  
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserContext(
                user_id=str(auth_result.user.id),
                username=auth_result.user.username,
                role=auth_result.user.role,
                permissions=await reference_manager.get_user_permissions(
                    str(auth_result.user.id)
                )
            )
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Login failed"
        )

@router.post("/logout")
async def logout(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """Log out user and terminate session."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "user_id": str(current_user.user_id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # End session
        await auth_service.end_session(
            token_id=current_user.token_id,
            user_id=str(current_user.user_id),
            context=context
        )

        # Update metrics
        await performance_service.update_auth_metrics(
            user_id=str(current_user.user_id),
            metrics={"last_logout": datetime.utcnow()}
        )

        logger.info(
            "User logged out",
            extra=context
        )

        return ServiceResponse(
            success=True,
            message="Successfully logged out",
            data={"timestamp": datetime.utcnow().isoformat()}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Logout failed"
        )

@router.post("/refresh")
async def refresh_token(
    request: Request,
    refresh_token: str,
    auth_service = Depends(get_auth_service)
) -> TokenResponse:
    """Refresh access token with enhanced validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.client.host,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Validate and refresh
        auth_result = await auth_service.refresh_token(
            refresh_token=refresh_token,
            context=context
        )

        logger.info(
            "Token refreshed",
            extra={
                **context,
                "user_id": str(auth_result.user.id)
            }
        )

        return TokenResponse(
            access_token=auth_result.session.access_token,
            refresh_token=auth_result.session.refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserContext(
                user_id=str(auth_result.user.id),
                username=auth_result.user.username,
                role=auth_result.user.role,
                permissions=await reference_manager.get_user_permissions(
                    str(auth_result.user.id)
                )
            )
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Token refresh failed"
        )

@router.get("/me", response_model=UserContext)
async def get_current_user(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user)
) -> UserContext:
    """Get current user details with validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "user_id": str(current_user.user_id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        logger.debug(
            "Retrieved user details",
            extra=context
        )
        return current_user

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get user details"
        )

@router.post("/password/reset")
async def reset_password(
    request: Request,
    email: str,
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """Initiate password reset with security checks."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.client.host,
        "email": email,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        # Initiate reset securely
        await auth_service.initiate_password_reset(
            email=email,
            context=context
        )

        logger.info(
            "Password reset initiated",
            extra={**context, "email": email}
        )

        return ServiceResponse(
            success=True,
            message="If an account exists with this email, reset instructions will be sent",
            data={"timestamp": datetime.utcnow().isoformat()}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Password reset initiation failed"
        )

@router.post("/password/reset/complete")
async def complete_reset(
    request: Request,
    token: str,
    new_password: str,
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """Complete password reset with validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.client.host,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        await auth_service.complete_password_reset(
            token=token,
            new_password=new_password,
            context=context
        )

        logger.info(
            "Password reset completed",
            extra=context
        )

        return ServiceResponse(
            success=True,
            message="Password reset successful",
            data={"timestamp": datetime.utcnow().isoformat()}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Password reset completion failed"
        )

@router.get("/sessions")
async def list_sessions(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """List active user sessions with details."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "user_id": str(current_user.user_id),
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        sessions = await auth_service.list_user_sessions(
            user_id=str(current_user.user_id),
            context=context
        )

        logger.info(
            "Retrieved user sessions",
            extra=context
        )

        return ServiceResponse(
            success=True,
            message="Active sessions retrieved",
            data={
                "sessions": sessions,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to list sessions"
        )

@router.post("/sessions/{session_id}/terminate")
async def terminate_session(
    session_id: str,
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """Terminate specific session with validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "user_id": str(current_user.user_id),
        "session_id": session_id,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        await auth_service.terminate_session(
            session_id=session_id,
            user_id=str(current_user.user_id),
            context=context
        )

        logger.info(
            "Session terminated",
            extra=context
        )

        return ServiceResponse(
            success=True,
            message="Session terminated successfully",
            data={"timestamp": datetime.utcnow().isoformat()}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to terminate session"
        )

@router.get("/permissions")
async def get_permissions(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service = Depends(get_auth_service)
) -> ServiceResponse:
    """Get user permissions with role validation."""
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "user_id": str(current_user.user_id),
        "role": current_user.role,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        permissions = await reference_manager.get_user_permissions(
            str(current_user.user_id)
        )

        logger.info(
            "Retrieved user permissions",
            extra=context
        )

        return ServiceResponse(
            success=True,
            message="User permissions retrieved",
            data={
                "permissions": permissions,
                "role": current_user.role,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context=context,
            log_message="Failed to get permissions"
        )

# Import services at end to avoid circular imports
from app.core.config import settings
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.api.v1.deps import get_auth_service, get_current_active_user