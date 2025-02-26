# File: app/services/auth/service.py

"""
Authentication service coordinating user authentication and authorization.

Features:
- User authentication (login)
- Password management
- Token generation
- Password change and reset functionality
- Login attempt tracking
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.core.errors.base import (
    AuthenticationError,
    ValidationError,
    NotFoundError,
)
from app.core.logging.logger import get_logger
from app.models.entities.user import User
from app.services.auth.password import password_manager
from app.services.auth.tokens import token_manager
from app.services.auth.tracking import login_tracker

# Note: validate_exchange_credentials() has been removed from this service 
# to separate exchange credential validation into the exchange layer.

class AuthenticationService:
    """
    Service for handling user authentication.
    """
    def __init__(self) -> None:
        # Instead of using getChild (which is not supported), we construct the logger name manually.
        self.logger = get_logger(__name__ + ".AuthenticationService")
        self.logger.info("Initializing Authentication Service")
    
    async def authenticate_user(
        self, username: str, password: str, ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate a user by verifying credentials and generating an access token.
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        if not user.is_active:
            raise AuthenticationError("User account is inactive", context={"username": username})
        
        valid_password = await password_manager.verify_password(password, user.hashed_password)
        await login_tracker.record_attempt(username=username, success=valid_password, ip_address=ip_address)
        if not valid_password:
            raise AuthenticationError("Invalid password", context={"username": username})
        
        access_token = await token_manager.create_access_token(subject=username, role=user.role)
        self.logger.info("User authenticated", extra={"username": username, "role": user.role, "ip_address": ip_address})
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "user": {
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active,
            },
        }
    
    async def change_password(
        self, username: str, current_password: str, new_password: str
    ) -> None:
        """
        Change the password for a user.
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        valid = await password_manager.verify_password(current_password, user.hashed_password)
        if not valid:
            raise AuthenticationError("Invalid current password", context={"username": username})
        
        strength = await password_manager.check_password_strength(new_password)
        if not strength["meets_requirements"]:
            raise ValidationError("New password does not meet requirements", context={"username": username, "requirements": strength})
        
        user.hashed_password = await password_manager.hash_password(new_password)
        user.password_changed_at = datetime.utcnow()
        await user.save()
        self.logger.info("Password changed", extra={"username": username, "changed_at": user.password_changed_at.isoformat()})
    
    async def reset_password(self, reset_token: str, new_password: str) -> None:
        """
        Reset a user's password using a reset token.
        """
        claims = await token_manager.decode_token(reset_token)
        username = claims["sub"]
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        strength = await password_manager.check_password_strength(new_password)
        if not strength["meets_requirements"]:
            raise ValidationError("Password does not meet requirements", context={"username": username, "requirements": strength})
        
        user.hashed_password = await password_manager.hash_password(new_password)
        user.password_changed_at = datetime.utcnow()
        await user.save()
        self.logger.info("Password reset", extra={"username": username, "reset_at": user.password_changed_at.isoformat()})
    
    async def create_reset_token(self, username: str) -> str:
        """
        Create a password reset token for a user.
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        reset_token = await token_manager.create_access_token(
            subject=username,
            expires_delta=timedelta(hours=24),
            additional_claims={"type": "password_reset"}
        )
        self.logger.info("Created reset token", extra={"username": username, "created_at": datetime.utcnow().isoformat()})
        return reset_token

# Global instance of the authentication service
auth_service = AuthenticationService()
