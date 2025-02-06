"""
Authentication service coordinating user authentication and authorization.

Features:
- User authentication
- Token management
- Password validation
- Authorization checks
- Login tracking
- Session management
"""

from typing import Dict, Optional, Any
from datetime import datetime, timedelta

from app.core.errors.base import (
    AuthenticationError,
    ValidationError,
    ExchangeError,
    NotFoundError
)
from app.core.logging.logger import get_logger

from .password import password_manager
from .tokens import token_manager
from .tracking import login_tracker

logger = get_logger(__name__)

class AuthService:
    """
    Authentication service coordinating authentication operations.
    
    Features:
    - User login/logout
    - Password management
    - Token validation
    - Access control
    """

    def __init__(self):
        """Initialize authentication service."""
        self.logger = logger.getChild("auth_service")

    async def validate_exchange_credentials(
        self,
        exchange: str,
        credentials: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate exchange API credentials.
        
        Args:
            exchange: Exchange type ("okx", "bybit", "bitget")
            credentials: Dict containing:
                - api_key: API key
                - api_secret: API secret
                - passphrase: Optional passphrase
                - testnet: Whether using testnet
                
        Returns:
            Dict containing:
                - valid: Whether credentials are valid
                - errors: List of any validation errors
                
        Raises:
            ValidationError: If credential format invalid
            AuthenticationError: If validation fails
        """
        try:
            # Validate required fields
            required = ["api_key", "api_secret"]
            missing = [f for f in required if not credentials.get(f)]
            if missing:
                raise ValidationError(
                    "Missing required credentials",
                    context={
                        "exchange": exchange,
                        "missing_fields": missing
                    }
                )

            # Validate credential format
            if len(credentials["api_key"]) < 16:
                raise ValidationError(
                    "API key too short",
                    context={
                        "exchange": exchange,
                        "min_length": 16
                    }
                )

            if len(credentials["api_secret"]) < 32:
                raise ValidationError(
                    "API secret too short", 
                    context={
                        "exchange": exchange,
                        "min_length": 32
                    }
                )

            # Get exchange service and validate
            from app.services.exchange.factory import get_exchange_client
            client = get_exchange_client(
                exchange_type=exchange,
                credentials=credentials
            )

            try:
                is_valid = await client.validate_credentials()
            except ExchangeError as e:
                return {
                    "valid": False,
                    "errors": [str(e)]
                }

            self.logger.info(
                "Validated exchange credentials",
                extra={
                    "exchange": exchange,
                    "testnet": credentials.get("testnet", False),
                    "valid": is_valid
                }
            )

            return {
                "valid": is_valid,
                "errors": [] if is_valid else ["Invalid credentials"]
            }

        except ValidationError:
            raise
        except Exception as e:
            raise AuthenticationError(
                "Credential validation failed",
                context={
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    async def authenticate_user(
        self,
        username: str,
        password: str,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate a user and generate access token.
        
        Args:
            username: User to authenticate
            password: Password to verify
            ip_address: Optional IP for tracking
            
        Returns:
            Dict containing token and user info
            
        Raises:
            AuthenticationError: If authentication fails
            NotFoundError: If user not found
        """
        try:
            # Get user from database
            from app.models.user import User
            user = await User.find_one({"username": username})
            
            if not user:
                raise NotFoundError(
                    "User not found",
                    context={"username": username}
                )

            if not user.is_active:
                raise AuthenticationError(
                    "User account is inactive",
                    context={"username": username}
                )

            # Verify password
            valid_password = await password_manager.verify_password(
                password,
                user.hashed_password
            )

            # Record attempt
            await login_tracker.record_attempt(
                username=username,
                success=valid_password,
                ip_address=ip_address
            )

            if not valid_password:
                raise AuthenticationError(
                    "Invalid password",
                    context={"username": username}
                )

            # Generate token
            access_token = await token_manager.create_access_token(
                subject=username,
                role=user.role
            )

            self.logger.info(
                "User authenticated",
                extra={
                    "username": username,
                    "role": user.role,
                    "ip_address": ip_address
                }
            )

            return {
                "access_token": access_token,
                "token_type": "Bearer",
                "user": {
                    "username": user.username,
                    "role": user.role,
                    "is_active": user.is_active
                }
            }

        except (AuthenticationError, NotFoundError):
            raise
        except Exception as e:
            raise AuthenticationError(
                "Authentication failed",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

    async def validate_token(
        self,
        token: str,
        required_role: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate a token and optionally check role.
        
        Args:
            token: Token to validate
            required_role: Optional role to check
            
        Returns:
            Dict with token claims
            
        Raises:
            AuthenticationError: If token invalid
            AuthorizationError: If role requirement not met
        """
        try:
            # Decode token
            claims = await token_manager.decode_token(token)
            
            # Verify role if required
            if required_role:
                token_role = claims.get("role")
                if token_role != required_role:
                    raise AuthorizationError(
                        "Insufficient privileges",
                        context={
                            "required_role": required_role,
                            "token_role": token_role
                        }
                    )

            return claims

        except (AuthenticationError, AuthorizationError):
            raise
        except Exception as e:
            raise AuthenticationError(
                "Token validation failed",
                context={
                    "error": str(e)
                }
            )

    async def register_user(
        self,
        username: str,
        password: str,
        role: str,
        created_by: str
    ) -> Dict[str, Any]:
        """
        Register a new user.
        
        Args:
            username: New username
            password: Password to set
            role: User role
            created_by: Admin creating user
            
        Returns:
            Dict with created user info
            
        Raises:
            ValidationError: If parameters invalid
            AuthenticationError: If registration fails
        """
        try:
            # Validate password strength
            strength = await password_manager.check_password_strength(password)
            if not strength["meets_requirements"]:
                raise ValidationError(
                    "Password does not meet requirements",
                    context={
                        "username": username,
                        "requirements": strength
                    }
                )

            # Check username availability
            from app.models.user import User
            existing = await User.find_one({"username": username})
            if existing:
                raise ValidationError(
                    "Username already exists",
                    context={"username": username}
                )

            # Hash password
            hashed_password = await password_manager.hash_password(password)

            # Create user
            user = User(
                username=username,
                hashed_password=hashed_password,
                role=role,
                created_by=created_by
            )
            await user.save()

            self.logger.info(
                "User registered",
                extra={
                    "username": username,
                    "role": role,
                    "created_by": created_by
                }
            )

            return {
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active
            }

        except ValidationError:
            raise
        except Exception as e:
            raise AuthenticationError(
                "User registration failed",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

    async def change_password(
        self,
        username: str,
        current_password: str,
        new_password: str
    ) -> None:
        """
        Change a user's password.
        
        Args:
            username: User to update
            current_password: Current password
            new_password: New password
            
        Raises:
            ValidationError: If passwords invalid
            AuthenticationError: If verification fails
        """
        try:
            # Get user
            from app.models.user import User
            user = await User.find_one({"username": username})
            if not user:
                raise NotFoundError(
                    "User not found",
                    context={"username": username}
                )

            # Verify current password
            valid = await password_manager.verify_password(
                current_password,
                user.hashed_password
            )
            if not valid:
                raise AuthenticationError(
                    "Invalid current password",
                    context={"username": username}
                )

            # Validate new password
            strength = await password_manager.check_password_strength(new_password)
            if not strength["meets_requirements"]:
                raise ValidationError(
                    "New password does not meet requirements",
                    context={
                        "username": username,
                        "requirements": strength
                    }
                )

            # Update password
            user.hashed_password = await password_manager.hash_password(new_password)
            user.password_changed_at = datetime.utcnow()
            await user.save()

            self.logger.info(
                "Password changed",
                extra={
                    "username": username,
                    "changed_at": user.password_changed_at.isoformat()
                }
            )

        except (ValidationError, AuthenticationError, NotFoundError):
            raise
        except Exception as e:
            raise AuthenticationError(
                "Password change failed",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

    async def reset_password(
        self,
        reset_token: str,
        new_password: str
    ) -> None:
        """
        Reset password using reset token.
        
        Args:
            reset_token: Valid reset token
            new_password: New password to set
            
        Raises:
            ValidationError: If password invalid
            AuthenticationError: If token invalid
        """
        try:
            # Verify token
            claims = await token_manager.decode_token(reset_token)
            username = claims["sub"]

            # Get user
            from app.models.user import User
            user = await User.find_one({"username": username})
            if not user:
                raise NotFoundError(
                    "User not found",
                    context={"username": username}
                )

            # Validate new password
            strength = await password_manager.check_password_strength(new_password)
            if not strength["meets_requirements"]:
                raise ValidationError(
                    "Password does not meet requirements",
                    context={
                        "username": username,
                        "requirements": strength
                    }
                )

            # Update password
            user.hashed_password = await password_manager.hash_password(new_password)
            user.password_changed_at = datetime.utcnow()
            await user.save()

            self.logger.info(
                "Password reset",
                extra={
                    "username": username,
                    "reset_at": user.password_changed_at.isoformat()
                }
            )

        except (ValidationError, AuthenticationError, NotFoundError):
            raise
        except Exception as e:
            raise AuthenticationError(
                "Password reset failed",
                context={
                    "error": str(e)
                }
            )

    async def create_reset_token(self, username: str) -> str:
        """
        Create a password reset token.
        
        Args:
            username: User to create token for
            
        Returns:
            str: Reset token
            
        Raises:
            NotFoundError: If user not found
            AuthenticationError: If token creation fails
        """
        try:
            # Verify user exists
            from app.models.user import User
            user = await User.find_one({"username": username})
            if not user:
                raise NotFoundError(
                    "User not found",
                    context={"username": username}
                )

            # Generate token
            reset_token = await token_manager.create_access_token(
                subject=username,
                expires_delta=timedelta(hours=24),
                additional_claims={"type": "password_reset"}
            )

            self.logger.info(
                "Created reset token",
                extra={
                    "username": username,
                    "created_at": datetime.utcnow().isoformat()
                }
            )

            return reset_token

        except NotFoundError:
            raise
        except Exception as e:
            raise AuthenticationError(
                "Failed to create reset token",
                context={
                    "username": username,
                    "error": str(e)
                }
            )

# Global instance
auth_service = AuthService()