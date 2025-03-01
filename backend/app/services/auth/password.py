"""
Password handling and validation service.

Features:
- Password hashing with bcrypt
- Password verification
- Password strength validation
- Reset token handling
"""

import asyncio
from typing import Dict, Union
import secrets

from passlib.context import CryptContext

from app.core.config import settings
from app.core.errors.base import ValidationError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
logger = get_logger(__name__)


class PasswordManager:
    """Handles password operations and validation."""

    def __init__(self) -> None:
        """Initialize with settings-based configuration."""
        self._min_length = getattr(settings.security, "MIN_PASSWORD_LENGTH", 8)
        self._max_length = getattr(settings.security, "MAX_PASSWORD_LENGTH", 128)
        self._min_complexity_score = getattr(settings.security, "MIN_PASSWORD_COMPLEXITY", 3)

    @error_handler("hash_password", log_message="Error hashing password")
    async def hash_password(self, password: str) -> str:
        """
        Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            str: Hashed password

        Raises:
            ValidationError: If the password is invalid or hashing fails.
        """
        self._validate_password_format(password)
        # Use asyncio.to_thread for CPU-bound operation
        return await asyncio.to_thread(pwd_context.hash, password)

    @error_handler("verify_password", log_message="Error verifying password")
    async def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.

        Args:
            plain_password: Password to verify.
            hashed_password: Hashed password to check against.

        Returns:
            bool: True if the password matches; otherwise, False.

        Raises:
            ValidationError: If verification fails.
        """
        # Use asyncio.to_thread for CPU-bound operation
        return await asyncio.to_thread(pwd_context.verify, plain_password, hashed_password)

    @error_handler("check_password_strength", log_message="Error checking password strength")
    async def check_password_strength(self, password: str) -> Dict[str, Union[bool, int]]:
        """
        Check password strength against several criteria:
          - Length (must be at least 8 characters)
          - Contains uppercase letters
          - Contains lowercase letters
          - Contains digits
          - Contains special characters

        Args:
            password: The password to evaluate.

        Returns:
            A dictionary containing:
              - The result of each metric as booleans.
              - The overall score (an integer).
              - A flag indicating if the password meets the minimum complexity requirements.
        """
        metrics = {
            "length": len(password) >= self._min_length,
            "uppercase": any(c.isupper() for c in password),
            "lowercase": any(c.islower() for c in password),
            "digits": any(c.isdigit() for c in password),
            "special": any(not c.isalnum() for c in password),
        }
        score = sum(metrics.values())
        meets_requirements = score >= self._min_complexity_score
        return {**metrics, "score": score, "meets_requirements": meets_requirements}

    def _validate_password_format(self, password: str) -> None:
        """
        Validate the basic format of the password.

        Args:
            password: The password to validate.

        Raises:
            ValidationError: If the password is not a string or its length is invalid.
        """
        if not isinstance(password, str):
            raise ValidationError(
                "Password must be a string",
                context={"type": type(password).__name__},
            )
        if not self._min_length <= len(password) <= self._max_length:
            raise ValidationError(
                "Invalid password length",
                context={"min_length": self._min_length, "max_length": self._max_length},
            )

    @error_handler("generate_reset_token", log_message="Error generating reset token")
    async def generate_reset_token(self) -> str:
        """Generate a secure password reset token."""
        # Use asyncio.to_thread for potential CPU-bound operation
        return await asyncio.to_thread(secrets.token_urlsafe, 32)