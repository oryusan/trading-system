"""
JWT token management with blacklisting support.

Features:
- Token creation and validation
- Token blacklist management
- Token metadata handling
- Secure token operations
"""

from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import secrets
from jose import jwt, JWTError

from app.core.errors.base import AuthenticationError
from app.core.logging.logger import get_logger
from app.core.config import settings

logger = get_logger(__name__)

class TokenManager:
    """Manages JWT tokens and blacklist."""

    def __init__(self):
        """Initialize token manager."""
        self._algorithm = settings.ALGORITHM
        self._secret_key = settings.SECRET_KEY
        self._blacklist: Dict[str, datetime] = {}

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
            subject: Token subject (usually username)
            role: Optional user role
            expires_delta: Optional expiration override
            additional_claims: Optional extra JWT claims
            
        Returns:
            str: Encoded JWT token
            
        Raises:
            AuthenticationError: If token creation fails
        """
        try:
            expire = datetime.utcnow() + (
                expires_delta or 
                timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            )
            
            token_id = secrets.token_hex(8)
            
            claims = {
                "sub": str(subject),
                "exp": expire,
                "iat": datetime.utcnow(),
                "jti": token_id,
                "role": role
            }
            
            if additional_claims:
                claims.update(additional_claims)
                
            token = jwt.encode(
                claims,
                self._secret_key,
                algorithm=self._algorithm
            )
            
            logger.info(
                "Created access token",
                extra={
                    "subject": subject,
                    "role": role,
                    "token_id": token_id
                }
            )
            
            return token

        except Exception as e:
            raise AuthenticationError(
                "Failed to create access token",
                context={
                    "subject": subject,
                    "role": role,
                    "error": str(e)
                }
            )

    async def decode_token(self, token: str) -> Dict[str, Any]:
        """
        Decode and validate a JWT token.
        
        Args:
            token: JWT token to decode
            
        Returns:
            Dict: Decoded token claims
            
        Raises:
            AuthenticationError: If token is invalid
        """
        try:
            # Check blacklist
            claims = jwt.get_unverified_claims(token)
            token_id = claims.get("jti")
            
            if token_id and self.is_blacklisted(token_id):
                raise AuthenticationError(
                    "Token has been revoked",
                    context={"token_id": token_id}
                )
                
            # Decode and verify
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm]
            )
            
            # Validate expiration
            if datetime.fromtimestamp(payload["exp"]) < datetime.utcnow():
                raise AuthenticationError(
                    "Token has expired",
                    context={"token_id": token_id}
                )
                
            return payload

        except JWTError as e:
            raise AuthenticationError(
                "Invalid token",
                context={"error": str(e)}
            )
        except Exception as e:
            raise AuthenticationError(
                "Token validation failed", 
                context={"error": str(e)}
            )

    def blacklist_token(self, token_id: str, expiry: datetime) -> None:
        """
        Add a token to the blacklist.
        
        Args:
            token_id: Token ID to blacklist
            expiry: When token expires
        """
        self._blacklist[token_id] = expiry
        self._cleanup_blacklist()
        
        logger.info(
            "Blacklisted token",
            extra={
                "token_id": token_id,
                "expiry": expiry.isoformat()
            }
        )

    def is_blacklisted(self, token_id: str) -> bool:
        """
        Check if a token is blacklisted.
        
        Args:
            token_id: Token ID to check
            
        Returns:
            bool: True if token is blacklisted
        """
        if token_id in self._blacklist:
            if datetime.utcnow() > self._blacklist[token_id]:
                self._blacklist.pop(token_id)
                return False
            return True
        return False

    def _cleanup_blacklist(self) -> None:
        """Remove expired tokens from blacklist."""
        now = datetime.utcnow()
        expired = [
            token_id for token_id, expiry 
            in self._blacklist.items()
            if expiry < now
        ]
        for token_id in expired:
            self._blacklist.pop(token_id)

    async def get_token_metadata(self, token: str) -> Dict[str, Any]:
        """
        Get metadata about a token.
        
        Args:
            token: JWT token
            
        Returns:
            Dict: Token metadata
        """
        payload = await self.decode_token(token)
        return {
            "subject": payload["sub"],
            "role": payload.get("role"),
            "issued_at": datetime.fromtimestamp(payload["iat"]),
            "expires_at": datetime.fromtimestamp(payload["exp"]),
            "token_id": payload["jti"]
        }

# Global instance
token_manager = TokenManager()