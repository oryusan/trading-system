"""
Login attempt tracking with rate limiting and lockout functionality.

Features:
- Login attempt tracking 
- Rate limiting
- Account lockouts
- Centralized error handling via a decorator
- Audit logging
- Redis persistence for attempt tracking
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import asyncio
import json

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.config import settings
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
    - Persists attempts and lockouts in Redis
    - Thread-safe tracking with locks
    """

    def __init__(self) -> None:
        """
        Initialize login tracking with settings-based configuration.
        """
        self._max_attempts = getattr(settings.security, "MAX_LOGIN_ATTEMPTS", 5)
        self._lockout_duration = timedelta(
            minutes=getattr(settings.security, "LOCKOUT_MINUTES", 30)
        )
        self._attempt_window = timedelta(
            minutes=getattr(settings.security, "ATTEMPT_WINDOW_MINUTES", 5)
        )
        self._lock = asyncio.Lock()
        self._redis_prefix = getattr(settings.redis, "LOGIN_ATTEMPT_PREFIX", "login_attempt:")
        self._redis_lockout_prefix = getattr(settings.redis, "LOGIN_LOCKOUT_PREFIX", "login_lockout:")
        self._redis: Optional[Redis] = None
        self.logger = get_logger("login_tracker")

    async def _get_redis(self) -> Redis:
        """Get or initialize Redis connection."""
        if self._redis is None:
            self._redis = Redis.from_url(
                getattr(settings.redis, "REDIS_URL", "redis://localhost:6379/0"),
                encoding="utf-8", 
                decode_responses=True
            )
        return self._redis

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
            redis = await self._get_redis()
            now = datetime.utcnow()
            await self._cleanup(now=now)

            if await self.is_locked_out(username, now=now):
                lockout_until = await self._get_lockout_time(username)
                if lockout_until:
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
                # Clear attempts on successful login
                await redis.delete(f"{self._redis_prefix}{username}")
                await redis.delete(f"{self._redis_lockout_prefix}{username}")
                self.logger.info(
                    "Successful login",
                    extra={"username": username, "ip_address": ip_address}
                )
            else:
                # Record failed attempt with timestamp
                attempt_key = f"{self._redis_prefix}{username}"
                attempts_json = await redis.get(attempt_key)
                
                attempts: List[str] = json.loads(attempts_json) if attempts_json else []
                attempts.append(now.isoformat())
                
                # Store updated attempts with window expiration
                await redis.setex(
                    attempt_key,
                    int(self._attempt_window.total_seconds()),
                    json.dumps(attempts)
                )
                
                # Check if we need to lock out
                if len(attempts) >= self._max_attempts:
                    lockout_until = now + self._lockout_duration
                    # Store lockout with expiration
                    await redis.setex(
                        f"{self._redis_lockout_prefix}{username}",
                        int(self._lockout_duration.total_seconds()),
                        lockout_until.isoformat()
                    )
                    
                    self.logger.warning(
                        "Account locked out",
                        extra={
                            "username": username,
                            "ip_address": ip_address,
                            "attempts": len(attempts),
                            "lockout_until": lockout_until.isoformat()
                        }
                    )
                    raise RateLimitError(
                        "Too many failed attempts",
                        context={
                            "username": username,
                            "attempts": len(attempts),
                            "lockout_until": lockout_until.isoformat()
                        }
                    )
                self.logger.warning(
                    "Failed login attempt",
                    extra={
                        "username": username,
                        "ip_address": ip_address,
                        "attempts": len(attempts)
                    }
                )

    async def _get_lockout_time(self, username: str) -> Optional[datetime]:
        """Get lockout time for a username."""
        redis = await self._get_redis()
        lockout_str = await redis.get(f"{self._redis_lockout_prefix}{username}")
        if lockout_str:
            return datetime.fromisoformat(lockout_str)
        return None

    async def is_locked_out(self, username: str, now: Optional[datetime] = None) -> bool:
        """
        Check if a username is currently locked out.

        Args:
            username: Username to check.
            now: Optional current time for comparison.

        Returns:
            True if the account is locked out, False otherwise.
        """
        now = now or datetime.utcnow()
        lockout_until = await self._get_lockout_time(username)
        return bool(lockout_until and lockout_until > now)

    async def _cleanup(self, now: Optional[datetime] = None) -> None:
        """
        Clean up outdated login attempts and expired lockouts.
        
        Note: Redis TTL handles most cleanup automatically, this is just a safeguard.
        
        Args:
            now: Optional current time; defaults to datetime.utcnow().
        """
        # Redis handles expiration automatically via the TTL setting
        # This method is kept for API compatibility but doesn't need to do anything
        pass

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
            redis = await self._get_redis()
            now = datetime.utcnow()
            
            # Get current attempts
            attempts_json = await redis.get(f"{self._redis_prefix}{username}")
            attempts = len(json.loads(attempts_json)) if attempts_json else 0
            
            # Check lockout status
            locked = await self.is_locked_out(username, now=now)
            
            # Calculate remaining lockout time
            remaining = 0
            if locked:
                lockout_until = await self._get_lockout_time(username)
                if lockout_until:
                    remaining = (lockout_until - now).total_seconds()
            
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
            redis = await self._get_redis()
            await redis.delete(f"{self._redis_prefix}{username}")
            await redis.delete(f"{self._redis_lockout_prefix}{username}")
            self.logger.info("Reset login attempts", extra={"username": username})

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None