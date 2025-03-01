"""
JWT token management with Redis-based blacklist support.

Features:
- Token creation and validation
- Blacklist management with Redis persistence
- Token metadata handling
- Uses a fallback default for ALGORITHM if not provided in settings.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import secrets

from jose import jwt, JWTError
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.config import settings
from app.core.errors.base import AuthenticationError
from app.core.errors.decorators import error_handler
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class TokenMetadata(BaseModel):
    subject: str
    role: Optional[str] = None
    issued_at: datetime
    expires_at: datetime
    token_id: str


class TokenManager:
    """Manages JWT tokens and blacklist with Redis persistence."""
    def __init__(self) -> None:
        """Initialize with settings-based configuration."""
        # Use getattr to provide a default if ALGORITHM is missing.
        self._algorithm = getattr(settings.security, "ALGORITHM", "HS256")
        self._secret_key = getattr(settings.security, "SECRET_KEY", "default-secret-key")
        self._redis_prefix = getattr(settings.redis, "TOKEN_BLACKLIST_PREFIX", "token_blacklist:")
        # Redis connection - lazily initialized
        self._redis: Optional[Redis] = None

    async def _get_redis(self) -> Redis:
        """Get or initialize Redis connection."""
        if self._redis is None:
            self._redis = Redis.from_url(
                getattr(settings.redis, "REDIS_URL", "redis://localhost:6379/0"),
                encoding="utf-8", 
                decode_responses=True
            )
        return self._redis

    @error_handler("create_access_token", log_message="Error creating access token")
    async def create_access_token(
        self,
        subject: str,
        role: Optional[str] = None,
        expires_delta: Optional[timedelta] = None,
        additional_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a JWT access token.
        
        Args:
            subject: The subject (typically username) of the token
            role: Optional user role
            expires_delta: Optional custom expiration time
            additional_claims: Optional additional claims to include
            
        Returns:
            str: The encoded JWT token
        """
        now = datetime.utcnow()
        expire = now + (expires_delta or timedelta(minutes=getattr(settings.security, "ACCESS_TOKEN_EXPIRE_MINUTES", 1440)))
        token_id = secrets.token_hex(8)
        claims: Dict[str, Any] = {
            "sub": str(subject),
            "exp": expire,
            "iat": now,
            "jti": token_id,
            "role": role,
        }
        if additional_claims:
            claims.update(additional_claims)
        
        # Using to_thread for CPU-bound operation
        token = await asyncio.to_thread(
            jwt.encode, claims, self._secret_key, algorithm=self._algorithm
        )
        logger.info("Created access token", extra={"subject": subject, "role": role, "token_id": token_id})
        return token

    @error_handler("decode_token", log_message="Error decoding token")
    async def decode_token(self, token: str) -> Dict[str, Any]:
        """
        Decode and validate a JWT token.
        
        Args:
            token: The JWT token to decode
            
        Returns:
            Dict[str, Any]: The token claims
            
        Raises:
            AuthenticationError: If the token is invalid, expired, or blacklisted
        """
        # Get unverified claims first to check blacklist
        unverified_claims = jwt.get_unverified_claims(token)
        token_id = unverified_claims.get("jti")
        
        # Check blacklist
        if token_id and await self.is_blacklisted(token_id):
            raise AuthenticationError("Token has been revoked", context={"token_id": token_id})
        
        # Using to_thread for CPU-bound operation
        payload = await asyncio.to_thread(
            jwt.decode, token, self._secret_key, algorithms=[self._algorithm]
        )
        
        # Check expiration
        now = datetime.utcnow()
        if datetime.fromtimestamp(payload["exp"]) < now:
            raise AuthenticationError("Token has expired", context={"token_id": token_id})
            
        return payload

    async def blacklist_token(self, token_id: str, expiry: datetime) -> None:
        """
        Add a token to the blacklist with expiration.
        
        Args:
            token_id: The token ID to blacklist
            expiry: When the token expires
        """
        redis = await self._get_redis()
        
        # Calculate seconds until expiry
        now = datetime.utcnow()
        if expiry > now:
            # Convert timedelta to seconds for Redis TTL
            ttl_seconds = int((expiry - now).total_seconds())
            # Store in Redis with automatic expiration
            await redis.setex(
                f"{self._redis_prefix}{token_id}", 
                ttl_seconds,
                "1"
            )
            logger.info("Blacklisted token", extra={"token_id": token_id, "expiry": expiry.isoformat()})
        else:
            logger.warning("Attempted to blacklist already expired token", 
                          extra={"token_id": token_id, "expiry": expiry.isoformat()})

    async def is_blacklisted(self, token_id: str) -> bool:
        """
        Check if a token is blacklisted.
        
        Args:
            token_id: The token ID to check
            
        Returns:
            bool: True if the token is blacklisted, False otherwise
        """
        redis = await self._get_redis()
        return bool(await redis.exists(f"{self._redis_prefix}{token_id}"))

    async def get_token_metadata(self, token: str) -> TokenMetadata:
        """
        Get metadata for a token.
        
        Args:
            token: The JWT token
            
        Returns:
            TokenMetadata: The token metadata
        """
        payload = await self.decode_token(token)
        return TokenMetadata(
            subject=payload["sub"],
            role=payload.get("role"),
            issued_at=datetime.fromtimestamp(payload["iat"]),
            expires_at=datetime.fromtimestamp(payload["exp"]),
            token_id=payload["jti"],
        )

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None