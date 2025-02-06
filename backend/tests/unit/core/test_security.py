import pytest
from datetime import datetime, timedelta
from jose import jwt
from app.core.security import (
    create_access_token,
    decode_token,
    verify_password,
    get_password_hash,
    login_tracker,
    TokenData,
    PasswordResetToken
)
from app.core.errors import AuthenticationError, ValidationError, RateLimitError

async def test_password_hashing():
    """Test password hashing and verification."""
    password = "test_password123"
    hashed = get_password_hash(password)
    
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)

async def test_token_creation_and_validation():
    """Test JWT token creation and validation."""
    username = "test_user"
    token = await create_access_token(subject=username)
    
    # Decode and validate token
    token_data = await decode_token(token)
    assert token_data.username == username
    assert isinstance(token_data.exp, datetime)

async def test_token_expiration():
    """Test token expiration handling."""
    # Create token that expires immediately
    token = await create_access_token(
        subject="test_user",
        expires_delta=timedelta(seconds=0)
    )
    
    # Wait a moment to ensure token expires
    await asyncio.sleep(1)
    
    # Verify token is expired
    with pytest.raises(AuthenticationError) as exc_info:
        await decode_token(token)
    assert "Token has expired" in str(exc_info.value)

async def test_login_attempt_tracking():
    """Test login attempt tracking and rate limiting."""
    username = "test_user"
    
    # Record multiple failed attempts
    for _ in range(4):
        login_tracker.record_attempt(username, success=False)
    
    # Verify user isn't locked out yet
    assert not login_tracker.is_locked_out(username)
    
    # Record one more failed attempt
    with pytest.raises(RateLimitError) as exc_info:
        login_tracker.record_attempt(username, success=False)
    assert "Too many failed login attempts" in str(exc_info.value)
    
    # Verify user is now locked out
    assert login_tracker.is_locked_out(username)
    
    # Record successful attempt
    login_tracker.record_attempt(username, success=True)
    assert not login_tracker.is_locked_out(username)

async def test_password_strength_check():
    """Test password strength evaluation."""
    # Test weak password
    weak_result = check_password_strength("weak")
    assert not weak_result["is_strong"]
    assert weak_result["score"] < 4
    
    # Test strong password
    strong_result = check_password_strength("StrongP@ss123")
    assert strong_result["is_strong"]
    assert strong_result["score"] >= 4
    assert strong_result["uppercase"]
    assert strong_result["lowercase"]
    assert strong_result["digits"]
    assert strong_result["special"]

async def test_password_reset_token():
    """Test password reset token functionality."""
    user_id = "test_user"
    
    # Create reset token
    reset_token = await PasswordResetToken.create(user_id)
    
    # Verify token
    verified_user_id = await PasswordResetToken.verify(reset_token)
    assert verified_user_id == user_id
    
    # Test invalid token type
    invalid_token = await create_access_token(subject=user_id)
    with pytest.raises(AuthenticationError) as exc_info:
        await PasswordResetToken.verify(invalid_token)
    assert "Invalid token type" in str(exc_info.value)

async def test_token_metadata():
    """Test token metadata extraction."""
    username = "test_user"
    role = "admin"
    
    token = await create_access_token(subject=username, role=role)
    metadata = await get_token_metadata(token)
    
    assert metadata.user_id == username
    assert metadata.role == role
    assert isinstance(metadata.issued_at, datetime)
    assert isinstance(metadata.expires_at, datetime)

async def test_token_blacklist():
    """Test token blacklisting functionality."""
    token_id = "test_token_123"
    expiry = datetime.utcnow() + timedelta(hours=1)
    
    token_blacklist.add_token(token_id, expiry)
    assert token_blacklist.is_blacklisted(token_id)
    
    # Test cleanup of expired tokens
    expired_token = "expired_token"
    expired_time = datetime.utcnow() - timedelta(hours=1)
    token_blacklist.add_token(expired_token, expired_time)
    token_blacklist._cleanup()
    assert not token_blacklist.is_blacklisted(expired_token)