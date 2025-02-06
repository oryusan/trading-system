"""
Login attempt tracking with rate limiting and lockout functionality.

Features:
- Login attempt tracking 
- Rate limiting
- Account lockouts
- Error handling
- Audit logging
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import asyncio

from app.core.errors import (
    RateLimitError, 
    ValidationError,
    DatabaseError
)
from app.core.logger import get_logger

logger = get_logger(__name__)

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
            max_attempts: Maximum failed attempts before lockout
            lockout_minutes: Minutes to lock account after max failures
            attempt_window_minutes: Window for tracking attempts
        """
        self._attempts: Dict[str, List[datetime]] = {}  # username -> list of attempts
        self._lockouts: Dict[str, datetime] = {}  # username -> lockout until
        self._max_attempts = max_attempts
        self._lockout_duration = timedelta(minutes=lockout_minutes)
        self._attempt_window = timedelta(minutes=attempt_window_minutes)
        self._lock = asyncio.Lock()
        self.logger = logger.getChild("login_tracker")

    async def record_attempt(
        self,
        username: str,
        success: bool,
        ip_address: Optional[str] = None
    ) -> None:
        """
        Record a login attempt with rate limiting.
        
        Args:
            username: Username attempting login
            success: Whether attempt was successful
            ip_address: Optional IP address of attempt
            
        Raises:
            RateLimitError: If rate limit exceeded
            ValidationError: If username invalid
        """
        if not username:
            raise ValidationError(
                "Invalid username",
                context={"username": username}
            )

        async with self._lock:
            try:
                # Clear old attempts
                self._cleanup()
                
                # Check current lockout
                if self.is_locked_out(username):
                    lockout_until = self._lockouts[username]
                    remaining = (lockout_until - datetime.utcnow()).total_seconds()
                    raise RateLimitError(
                        "Account is locked out",
                        context={
                            "username": username,
                            "lockout_until": lockout_until.isoformat(),
                            "remaining_seconds": remaining
                        }
                    )

                if success:
                    # Clear attempts on success
                    self._attempts.pop(username, None)
                    self._lockouts.pop(username, None)
                    
                    self.logger.info(
                        "Successful login",
                        extra={
                            "username": username,
                            "ip_address": ip_address
                        }
                    )
                else:
                    # Record failed attempt
                    if username not in self._attempts:
                        self._attempts[username] = []
                    self._attempts[username].append(datetime.utcnow())

                    # Check for lockout
                    if len(self._attempts[username]) >= self._max_attempts:
                        lockout_until = datetime.utcnow() + self._lockout_duration
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

            except (RateLimitError, ValidationError):
                raise
            except Exception as e:
                raise DatabaseError(
                    "Failed to record login attempt",
                    context={
                        "username": username,
                        "success": success,
                        "error": str(e)
                    }
                )

    def is_locked_out(self, username: str) -> bool:
        """
        Check if a username is currently locked out.
        
        Args:
            username: Username to check
            
        Returns:
            bool: True if account is locked out
        """
        if username in self._lockouts:
            if datetime.utcnow() > self._lockouts[username]:
                self._lockouts.pop(username)
                return False
            return True
        return False

    def _cleanup(self) -> None:
        """Clean up old attempts and expired lockouts."""
        now = datetime.utcnow()
        window_start = now - self._attempt_window
        
        # Clean old attempts
        self._attempts = {
            username: [
                attempt for attempt in attempts 
                if attempt > window_start
            ]
            for username, attempts in self._attempts.items()
        }

        # Clean expired lockouts  
        self._lockouts = {
            username: lockout
            for username, lockout in self._lockouts.items()
            if lockout > now
        }

    async def get_attempt_info(
        self,
        username: str
    ) -> Dict[str, Any]:
        """
        Get login attempt information for a username.
        
        Args:
            username: Username to get info for
            
        Returns:
            Dict containing:
            - recent_attempts: Number of recent attempts
            - is_locked: Whether account is locked
            - lockout_remaining: Seconds until lockout expires
        """
        async with self._lock:
            self._cleanup()
            
            attempts = len(self._attempts.get(username, []))
            is_locked = self.is_locked_out(username)
            remaining = 0
            
            if is_locked:
                lockout_until = self._lockouts[username]
                remaining = (lockout_until - datetime.utcnow()).total_seconds()

            return {
                "recent_attempts": attempts,
                "is_locked": is_locked,
                "lockout_remaining": remaining
            }

    async def reset_attempts(self, username: str) -> None:
        """
        Reset login attempts and lockout for a username.
        
        Args:
            username: Username to reset
        """
        async with self._lock:
            self._attempts.pop(username, None)
            self._lockouts.pop(username, None)
            
            self.logger.info(
                "Reset login attempts",
                extra={"username": username}
            )

# Global instance
login_tracker = LoginTracker()