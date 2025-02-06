import pytest
from pydantic import ValidationError
from app.core.config import Settings

async def test_valid_settings():
    """Test creating Settings with valid configuration."""
    settings = Settings(
        MONGODB_URL="mongodb://localhost:27017",
        MONGODB_DB_NAME="test_db",
        TRADINGVIEW_WEBHOOK_SECRET="test-secret",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="test-chat"
    )
    assert settings.MONGODB_URL == "mongodb://localhost:27017"
    assert settings.MONGODB_DB_NAME == "test_db"
    assert settings.ERROR_NOTIFICATION_LEVELS == ["CRITICAL", "ERROR"]

async def test_invalid_mongodb_connections():
    """Test validation of MongoDB connection settings."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            MONGODB_URL="mongodb://localhost:27017",
            MONGODB_DB_NAME="test_db",
            MONGODB_MIN_CONNECTIONS=10,
            MONGODB_MAX_CONNECTIONS=5  # Invalid: min > max
        )
    assert "MIN_CONNECTIONS cannot be greater than MAX_CONNECTIONS" in str(exc_info.value)

async def test_cors_origins_validation():
    """Test CORS origins validation and formatting."""
    # Test comma-separated string
    settings = Settings(
        MONGODB_URL="mongodb://localhost:27017",
        MONGODB_DB_NAME="test_db",
        TRADINGVIEW_WEBHOOK_SECRET="test-secret",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="test-chat",
        BACKEND_CORS_ORIGINS="http://localhost:3000,http://localhost:8000"
    )
    assert len(settings.BACKEND_CORS_ORIGINS) == 2
    assert "http://localhost:3000" in settings.BACKEND_CORS_ORIGINS

    # Test list input
    settings = Settings(
        MONGODB_URL="mongodb://localhost:27017",
        MONGODB_DB_NAME="test_db",
        TRADINGVIEW_WEBHOOK_SECRET="test-secret",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="test-chat",
        BACKEND_CORS_ORIGINS=["http://localhost:3000"]
    )
    assert len(settings.BACKEND_CORS_ORIGINS) == 1

async def test_log_level_validation():
    """Test log level validation."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            MONGODB_URL="mongodb://localhost:27017",
            MONGODB_DB_NAME="test_db",
            TRADINGVIEW_WEBHOOK_SECRET="test-secret",
            TELEGRAM_BOT_TOKEN="test-token",
            TELEGRAM_CHAT_ID="test-chat",
            LOG_LEVEL="INVALID_LEVEL"
        )
    assert "LOG_LEVEL must be one of" in str(exc_info.value)