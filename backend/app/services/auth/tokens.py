"""
JWT token management with blacklist support.

Features:
- Token creation and validation
- Blacklist management
- Token metadata handling
- Uses a fallback default for ALGORITHM if not provided in settings.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import secrets

from jose import jwt, JWTError
from pydantic import BaseModel

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
    """Manages JWT tokens and blacklist."""
    def __init__(self) -> None:
        # Use getattr to provide a default if ALGORITHM is missing.
        self._algorithm = getattr(settings, "ALGORITHM", "HS256")
        self._secret_key = getattr(settings, "SECRET_KEY", "default-secret-key")
        self._blacklist: Dict[str, datetime] = {}

    @error_handler("create_access_token", log_message="Error creating access token")
    def create_access_token(
        self,
        subject: str,
        role: Optional[str] = None,
        expires_delta: Optional[timedelta] = None,
        additional_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        now = datetime.utcnow()
        expire = now + (expires_delta or timedelta(minutes=getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 1440)))
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
        token = jwt.encode(claims, self._secret_key, algorithm=self._algorithm)
        logger.info("Created access token", extra={"subject": subject, "role": role, "token_id": token_id})
        return token

    @error_handler("decode_token", log_message="Error decoding token")
    def decode_token(self, token: str) -> Dict[str, Any]:
        unverified_claims = jwt.get_unverified_claims(token)
        token_id = unverified_claims.get("jti")
        if token_id and self.is_blacklisted(token_id):
            raise AuthenticationError("Token has been revoked", context={"token_id": token_id})
        payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
        now = datetime.utcnow()
        if datetime.fromtimestamp(payload["exp"]) < now:
            raise AuthenticationError("Token has expired", context={"token_id": token_id})
        return payload

    def blacklist_token(self, token_id: str, expiry: datetime) -> None:
        self._blacklist[token_id] = expiry
        self._cleanup_blacklist()
        logger.info("Blacklisted token", extra={"token_id": token_id, "expiry": expiry.isoformat()})

    def is_blacklisted(self, token_id: str) -> bool:
        if token_id in self._blacklist:
            if datetime.utcnow() > self._blacklist[token_id]:
                self._blacklist.pop(token_id)
                return False
            return True
        return False

    def _cleanup_blacklist(self) -> None:
        now = datetime.utcnow()
        expired_tokens = [tid for tid, expiry in self._blacklist.items() if expiry < now]
        for tid in expired_tokens:
            self._blacklist.pop(tid)

    def get_token_metadata(self, token: str) -> TokenMetadata:
        payload = self.decode_token(token)
        return TokenMetadata(
            subject=payload["sub"],
            role=payload.get("role"),
            issued_at=datetime.fromtimestamp(payload["iat"]),
            expires_at=datetime.fromtimestamp(payload["exp"]),
            token_id=payload["jti"],
        )


# Global instance of TokenManager
token_manager = TokenManager()
