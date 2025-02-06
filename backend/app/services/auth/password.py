"""
Password handling and validation service.

Features:
- Password hashing with bcrypt
- Password verification
- Password strength validation
- Reset token handling
"""

from typing import Dict, Optional, Union
from passlib.context import CryptContext
import secrets
from datetime import datetime

from app.core.errors.base import ValidationError
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class PasswordManager:
    """Handles password operations and validation."""

    def __init__(self):
        """Initialize password manager."""
        self._min_length = 8
        self._max_length = 128
        self._min_complexity_score = 3

    async def hash_password(self, password: str) -> str:
        """
        Hash a password using bcrypt.
        
        Args:
            password: Plain text password
            
        Returns:
            str: Hashed password
            
        Raises:
            ValidationError: If password is invalid
        """
        try:
            self._validate_password_format(password)
            return pwd_context.hash(password)
        except Exception as e:
            raise ValidationError(
                "Password hashing failed",
                context={"error": str(e)}
            )

    async def verify_password(
        self,
        plain_password: str,
        hashed_password: str
    ) -> bool:
        """
        Verify a password against its hash.
        
        Args:
            plain_password: Password to verify
            hashed_password: Hash to check against
            
        Returns:
            bool: True if password matches
            
        Raises:
            ValidationError: If verification fails
        """
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as e:
            raise ValidationError(
                "Password verification failed",
                context={"error": str(e)}
            )

    async def check_password_strength(self, password: str) -> Dict[str, Union[bool, int]]:
        """
        Check password strength against requirements.
        
        Checks:
        - Length (8-128 chars)
        - Contains uppercase
        - Contains lowercase  
        - Contains numbers
        - Contains special chars
        
        Args:
            password: Password to check
            
        Returns:
            Dict with strength metrics and overall score
            
        Raises:
            ValidationError: If password is invalid
        """
        try:
            metrics = {
                "length": len(password) >= self._min_length,
                "uppercase": any(c.isupper() for c in password),
                "lowercase": any(c.islower() for c in password),
                "digits": any(c.isdigit() for c in password),
                "special": any(not c.isalnum() for c in password)
            }
            
            score = sum(1 for passed in metrics.values() if passed)
            meets_requirements = score >= self._min_complexity_score
            
            return {
                **metrics,
                "score": score,
                "meets_requirements": meets_requirements
            }

        except Exception as e:
            raise ValidationError(
                "Password strength check failed",
                context={"error": str(e)}
            )

    def _validate_password_format(self, password: str) -> None:
        """
        Validate basic password format.
        
        Args:
            password: Password to validate
            
        Raises:
            ValidationError: If password format is invalid
        """
        if not isinstance(password, str):
            raise ValidationError(
                "Password must be a string",
                context={"type": type(password).__name__}
            )
            
        if not self._min_length <= len(password) <= self._max_length:
            raise ValidationError(
                "Invalid password length",
                context={
                    "min_length": self._min_length,
                    "max_length": self._max_length
                }
            )

    async def generate_reset_token(self) -> str:
        """Generate a secure password reset token."""
        return secrets.token_urlsafe(32)

# Global instance
password_manager = PasswordManager()