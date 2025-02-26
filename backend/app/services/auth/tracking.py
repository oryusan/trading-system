"""
Login attempt tracking with rate limiting and lockout functionality.

Features:
- Login attempt tracking 
- Rate limiting
- Account lockouts
- Centralized error handling via a decorator
- Audit logging
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import asyncio

from pydantic import BaseModel

from app.core.errors.base import RateLimitError, ValidationError, DatabaseError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class LoginAttemptInfo(BaseModel):
    recent_attempts: int
    is_locked: bool
    lockout_remaining: float


class LoginTracker:
    """
    Tracks login attempts with rate limiting and lockout management.

    Features:
    - Tracks failed login attempts per username
    - Enforces lockout after too many failures
    - Cleans up old attempts automatically
    - Thread-safe tracking with locks
    """

    def __init__(
        self,
        max_attempts: int = 5,
        lockout_minutes: int = 30,
        attempt_window_minutes: int = 5
    ):
        """
        Initialize login tracking.

        Args:
            max_attempts: Maximum failed attempts before lockout.
            lockout_minutes: Minutes to lock account after max failures.
            attempt_window_minutes: Window for tracking attempts.
        """
        self._attempts: Dict[str, List[datetime]] = {}  # username -> list of attempts
        self._lockouts: Dict[str, datetime] = {}  # username -> lockout until
        self._max_attempts = max_attempts
        self._lockout_duration = timedelta(minutes=lockout_minutes)
        self._attempt_window = timedelta(minutes=attempt_window_minutes)
        self._lock = asyncio.Lock()
        self.logger = get_logger("login_tracker")

    @error_handler("record_attempt", log_message="Error recording login attempt")
    async def record_attempt(
        self,
        username: str,
        success: bool,
        ip_address: Optional[str] = None
    ) -> None:
        """
        Record a login attempt with rate limiting.

        Args:
            username: Username attempting login.
            success: Whether the attempt was successful.
            ip_address: Optional IP address of the attempt.

        Raises:
            RateLimitError: If rate limit exceeded.
            ValidationError: If username is invalid.
            DatabaseError: For unexpected errors.
        """
        if not username:
            raise ValidationError("Invalid username", context={"username": username})

        async with self._lock:
            now = datetime.utcnow()
            self._cleanup(now=now)

            if self.is_locked_out(username, now=now):
                lockout_until = self._lockouts[username]
                remaining = (lockout_until - now).total_seconds()
                raise RateLimitError(
                    "Account is locked out",
                    context={
                        "username": username,
                        "lockout_until": lockout_until.isoformat(),
                        "remaining_seconds": remaining
                    }
                )

            if success:
                # Clear attempts on successful login.
                self._attempts.pop(username, None)
                self._lockouts.pop(username, None)
                self.logger.info(
                    "Successful login",
                    extra={"username": username, "ip_address": ip_address}
                )
            else:
                # Record failed attempt.
                self._attempts.setdefault(username, []).append(now)
                if len(self._attempts[username]) >= self._max_attempts:
                    lockout_until = now + self._lockout_duration
                    self._lockouts[username] = lockout_until
                    self.logger.warning(
                        "Account locked out",
                        extra={
                            "username": username,
                            "ip_address": ip_address,
                            "attempts": len(self._attempts[username]),
                            "lockout_until": lockout_until.isoformat()
                        }
                    )
                    raise RateLimitError(
                        "Too many failed attempts",
                        context={
                            "username": username,
                            "attempts": len(self._attempts[username]),
                            "lockout_until": lockout_until.isoformat()
                        }
                    )
                self.logger.warning(
                    "Failed login attempt",
                    extra={
                        "username": username,
                        "ip_address": ip_address,
                        "attempts": len(self._attempts[username])
                    }
                )

    def is_locked_out(self, username: str, now: Optional[datetime] = None) -> bool:
        """
        Check if a username is currently locked out.

        Args:
            username: Username to check.
            now: Optional current time for comparison.

        Returns:
            True if the account is locked out, False otherwise.
        """
        now = now or datetime.utcnow()
        if username in self._lockouts:
            if now > self._lockouts[username]:
                self._lockouts.pop(username)
                return False
            return True
        return False

    def _cleanup(self, now: Optional[datetime] = None) -> None:
        """
        Clean up outdated login attempts and expired lockouts.

        Args:
            now: Optional current time; defaults to datetime.utcnow().
        """
        now = now or datetime.utcnow()
        window_start = now - self._attempt_window

        # Remove outdated attempts.
        self._attempts = {
            user: [attempt for attempt in attempts if attempt > window_start]
            for user, attempts in self._attempts.items()
        }

        # Remove expired lockouts.
        self._lockouts = {
            user: lockout
            for user, lockout in self._lockouts.items()
            if lockout > now
        }

    @error_handler("get_attempt_info", log_message="Error retrieving login attempt info")
    async def get_attempt_info(self, username: str) -> LoginAttemptInfo:
        """
        Retrieve login attempt information for a username.

        Args:
            username: Username to retrieve info for.

        Returns:
            A LoginAttemptInfo instance containing:
              - recent_attempts: Number of recent failed attempts.
              - is_locked: Whether the account is locked.
              - lockout_remaining: Seconds until lockout expires (0 if not locked).
        """
        async with self._lock:
            now = datetime.utcnow()
            self._cleanup(now=now)
            attempts = len(self._attempts.get(username, []))
            locked = self.is_locked_out(username, now=now)
            remaining = (self._lockouts[username] - now).total_seconds() if locked else 0
            return LoginAttemptInfo(
                recent_attempts=attempts,
                is_locked=locked,
                lockout_remaining=remaining
            )

    @error_handler("reset_attempts", log_message="Error resetting login attempts")
    async def reset_attempts(self, username: str) -> None:
        """
        Reset login attempts and lockout status for a username.

        Args:
            username: Username for which to reset attempts.
        """
        async with self._lock:
            self._attempts.pop(username, None)
            self._lockouts.pop(username, None)
            self.logger.info("Reset login attempts", extra={"username": username})


# Global instance for use in the application.
login_tracker = LoginTracker()
