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

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from app.core.config import settings
from app.core.logging.logger import get_logger
from app.core.references import UserRole
from app.api.v1.references import (
    LoginRequest,
    TokenResponse,
    UserContext,
    DeviceInfo,
    ServiceResponse
)
from app.api.v1.deps import get_auth_service, get_current_active_user
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager

router = APIRouter()
logger = get_logger(__name__)

def current_timestamp() -> str:
    return datetime.utcnow().isoformat()

def build_context(request: Request, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    context = {
        "request_id": request.headers.get("X-Request-ID"),
        "ip_address": request.client.host,
        "timestamp": current_timestamp()
    }
    if extra:
        context.update(extra)
    return context

def get_device_info(request: Request) -> DeviceInfo:
    return DeviceInfo(
        ip_address=request.client.host,
        user_agent=request.headers.get("User-Agent", "unknown"),
        device_type=request.headers.get("Sec-CH-UA-Mobile", "unknown")
    )

async def create_token_response(auth_result) -> TokenResponse:
    permissions = await reference_manager.get_user_permissions(str(auth_result.user.id))
    return TokenResponse(
        access_token=auth_result.session.access_token,
        refresh_token=auth_result.session.refresh_token,
        token_type="bearer",
        expires_in=settings.security.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_context=UserContext(
            user_id=str(auth_result.user.id),
            username=auth_result.user.username,
            role=auth_result.user.role,
            permissions=permissions
        )
    )

@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    login_data: LoginRequest,
    auth_service=Depends(get_auth_service)
) -> TokenResponse:
    context = build_context(request, {
        "user_agent": request.headers.get("User-Agent"),
        "username": login_data.username
    })
    if not login_data.device_info:
        login_data.device_info = get_device_info(request)
    auth_result = await auth_service.authenticate_user(
        username=login_data.username,
        password=login_data.password,
        device_info=login_data.device_info,
        context=context
    )
    await performance_service.update_auth_metrics(
        user_id=str(auth_result.user.id),
        metrics={
            "last_login": datetime.utcnow(),
            "login_success": True,
            "device_info": login_data.device_info.dict()
        }
    )
    logger.info("User login successful", extra={**context, "user_id": str(auth_result.user.id)})
    return await create_token_response(auth_result)

@router.post("/logout")
async def logout(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service=Depends(get_auth_service)
) -> ServiceResponse:
    context = build_context(request, {"user_id": str(current_user.user_id)})
    await auth_service.end_session(
        token_id=current_user.token_id,
        user_id=str(current_user.user_id),
        context=context
    )
    await performance_service.update_auth_metrics(
        user_id=str(current_user.user_id),
        metrics={"last_logout": datetime.utcnow()}
    )
    logger.info("User logged out", extra=context)
    return ServiceResponse(
        success=True,
        message="Successfully logged out",
        data={"timestamp": current_timestamp()}
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    refresh_token: str,
    auth_service=Depends(get_auth_service)
) -> TokenResponse:
    context = build_context(request)
    auth_result = await auth_service.refresh_token(
        refresh_token=refresh_token,
        context=context
    )
    logger.info("Token refreshed", extra={**context, "user_id": str(auth_result.user.id)})
    return await create_token_response(auth_result)

@router.get("/me", response_model=UserContext)
async def get_current_user(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user)
) -> UserContext:
    context = build_context(request, {"user_id": str(current_user.user_id)})
    logger.debug("Retrieved user details", extra=context)
    return current_user

@router.get("/sessions")
async def list_sessions(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service=Depends(get_auth_service)
) -> ServiceResponse:
    context = build_context(request, {"user_id": str(current_user.user_id)})
    sessions = await auth_service.list_user_sessions(
        user_id=str(current_user.user_id),
        context=context
    )
    logger.info("Retrieved user sessions", extra=context)
    return ServiceResponse(
        success=True,
        message="Active sessions retrieved",
        data={"sessions": sessions, "timestamp": current_timestamp()}
    )

@router.post("/sessions/{session_id}/terminate")
async def terminate_session(
    session_id: str,
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service=Depends(get_auth_service)
) -> ServiceResponse:
    context = build_context(request, {"user_id": str(current_user.user_id), "session_id": session_id})
    await auth_service.terminate_session(
        session_id=session_id,
        user_id=str(current_user.user_id),
        context=context
    )
    logger.info("Session terminated", extra=context)
    return ServiceResponse(
        success=True,
        message="Session terminated successfully",
        data={"timestamp": current_timestamp()}
    )

@router.get("/permissions")
async def get_permissions(
    request: Request,
    current_user: UserContext = Depends(get_current_active_user),
    auth_service=Depends(get_auth_service)
) -> ServiceResponse:
    context = build_context(request, {"user_id": str(current_user.user_id), "role": current_user.role})
    permissions = await reference_manager.get_user_permissions(str(current_user.user_id))
    logger.info("Retrieved user permissions", extra=context)
    return ServiceResponse(
        success=True,
        message="User permissions retrieved",
        data={
            "permissions": permissions,
            "role": current_user.role,
            "timestamp": current_timestamp()
        }
    )
