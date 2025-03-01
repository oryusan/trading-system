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
from app.services.auth.password import PasswordManager
from app.services.auth.tokens import TokenManager
from app.services.auth.tracking import LoginTracker

class AuthenticationService:
    """
    Service for handling user authentication.
    """
    def __init__(
        self,
        password_manager: PasswordManager,
        token_manager: TokenManager,
        login_tracker: LoginTracker
    ) -> None:
        """
        Initialize the authentication service with its dependencies.
        
        Args:
            password_manager: For password operations
            token_manager: For JWT token operations
            login_tracker: For tracking login attempts
        """
        self.password_manager = password_manager
        self.token_manager = token_manager
        self.login_tracker = login_tracker
        # Construct the logger name manually
        self.logger = get_logger(__name__ + ".AuthenticationService")
        self.logger.info("Initializing Authentication Service")
    
    async def authenticate_user(
        self, username: str, password: str, ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate a user by verifying credentials and generating an access token.
        
        Args:
            username: User's username
            password: User's password
            ip_address: Optional IP address for tracking
            
        Returns:
            Dict with access token and user information
            
        Raises:
            NotFoundError: If user doesn't exist
            AuthenticationError: If credentials are invalid
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        if not user.is_active:
            raise AuthenticationError("User account is inactive", context={"username": username})
        
        valid_password = await self.password_manager.verify_password(password, user.hashed_password)
        await self.login_tracker.record_attempt(username=username, success=valid_password, ip_address=ip_address)
        if not valid_password:
            raise AuthenticationError("Invalid password", context={"username": username})
        
        access_token = await self.token_manager.create_access_token(subject=username, role=user.role)
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
        
        Args:
            username: User's username
            current_password: Current password for verification
            new_password: New password to set
            
        Raises:
            NotFoundError: If user doesn't exist
            AuthenticationError: If current password is invalid
            ValidationError: If new password doesn't meet requirements
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        valid = await self.password_manager.verify_password(current_password, user.hashed_password)
        if not valid:
            raise AuthenticationError("Invalid current password", context={"username": username})
        
        strength = await self.password_manager.check_password_strength(new_password)
        if not strength["meets_requirements"]:
            raise ValidationError("New password does not meet requirements", context={"username": username, "requirements": strength})
        
        user.hashed_password = await self.password_manager.hash_password(new_password)
        user.password_changed_at = datetime.utcnow()
        await user.save()
        self.logger.info("Password changed", extra={"username": username, "changed_at": user.password_changed_at.isoformat()})
    
    async def reset_password(self, reset_token: str, new_password: str) -> None:
        """
        Reset a user's password using a reset token.
        
        Args:
            reset_token: Valid reset token
            new_password: New password to set
            
        Raises:
            NotFoundError: If user doesn't exist
            ValidationError: If new password doesn't meet requirements
        """
        claims = await self.token_manager.decode_token(reset_token)
        username = claims["sub"]
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        strength = await self.password_manager.check_password_strength(new_password)
        if not strength["meets_requirements"]:
            raise ValidationError("Password does not meet requirements", context={"username": username, "requirements": strength})
        
        user.hashed_password = await self.password_manager.hash_password(new_password)
        user.password_changed_at = datetime.utcnow()
        await user.save()
        self.logger.info("Password reset", extra={"username": username, "reset_at": user.password_changed_at.isoformat()})
    
    async def create_reset_token(self, username: str) -> str:
        """
        Create a password reset token for a user.
        
        Args:
            username: User's username
            
        Returns:
            Reset token string
            
        Raises:
            NotFoundError: If user doesn't exist
        """
        user = await User.find_one({"username": username})
        if not user:
            raise NotFoundError("User not found", context={"username": username})
        
        reset_token = await self.token_manager.create_access_token(
            subject=username,
            expires_delta=timedelta(hours=24),
            additional_claims={"type": "password_reset"}
        )
        self.logger.info("Created reset token", extra={"username": username, "created_at": datetime.utcnow().isoformat()})
        return reset_token
    
    async def close(self) -> None:
        """Close all resources."""
        await self.token_manager.close()
        await self.login_tracker.close()


# Factory function to create the service with dependencies
def create_auth_service() -> AuthenticationService:
    """Create and return an AuthenticationService instance with all dependencies."""
    return AuthenticationService(
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
        login_tracker=LoginTracker()
    )

# Global instance for convenience
auth_service = create_auth_service()