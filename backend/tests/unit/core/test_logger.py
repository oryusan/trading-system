# File: tests/unit/core/test_logger.py

import pytest
import logging
import json
from pathlib import Path
from datetime import datetime
from app.core.logger import get_logger, setup_logging
from app.core.config import Settings

@pytest.fixture
def test_log_file(tmp_path):
    """Create a temporary log file for testing."""
    return tmp_path / "test.log"

@pytest.fixture
def test_settings(test_log_file):
    """Create test settings with logging configuration."""
    return Settings(
        MONGODB_URL="mongodb://localhost:27017",
        MONGODB_DB_NAME="test_db",
        TRADINGVIEW_WEBHOOK_SECRET="test-secret",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="test-chat",
        LOG_LEVEL="DEBUG",
        LOG_FORMAT="json",
        LOG_FILE_PATH=str(test_log_file)
    )

async def test_logger_initialization(test_settings):
    """Test basic logger initialization."""
    logger = get_logger(__name__)
    
    assert logger.name == __name__
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) > 0

async def test_json_log_formatting(test_log_file, test_settings):
    """Test JSON formatting of log messages."""
    # Setup logging with JSON format
    setup_logging(test_settings)
    logger = get_logger("test_json")
    
    # Log a test message with extra fields
    test_message = "Test JSON logging"
    extra_data = {
        "user_id": "test123",
        "action": "login",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    logger.info(test_message, extra=extra_data)
    
    # Read and parse the log file
    with open(test_log_file) as f:
        log_line = f.readline()
        log_data = json.loads(log_line)
    
    # Verify log structure
    assert log_data["message"] == test_message
    assert log_data["level"] == "INFO"
    assert log_data["user_id"] == extra_data["user_id"]
    assert log_data["action"] == extra_data["action"]
    assert "timestamp" in log_data
    assert "logger" in log_data

async def test_log_levels(test_log_file, test_settings):
    """Test different logging levels."""
    setup_logging(test_settings)
    logger = get_logger("test_levels")
    
    # Test all log levels
    test_message = "Test log level: {}"
    
    log_levels = {
        "debug": logger.debug,
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical
    }
    
    for level, log_func in log_levels.items():
        log_func(test_message.format(level))
    
    # Read log file and verify all levels
    with open(test_log_file) as f:
        logs = f.readlines()
    
    assert len(logs) == len(log_levels)
    for log, level in zip(logs, log_levels.keys()):
        log_data = json.loads(log)
        assert log_data["level"].lower() == level.upper()

async def test_error_logging_with_context(test_log_file, test_settings):
    """Test error logging with exception context."""
    setup_logging(test_settings)
    logger = get_logger("test_error")
    
    try:
        raise ValueError("Test exception")
    except Exception as e:
        logger.error(
            "Error occurred",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "context": {"test": "data"}
            },
            exc_info=True
        )
    
    # Verify error log
    with open(test_log_file) as f:
        log_data = json.loads(f.readline())
    
    assert log_data["message"] == "Error occurred"
    assert log_data["error"] == "Test exception"
    assert log_data["error_type"] == "ValueError"
    assert "traceback" in log_data
    assert log_data["context"] == {"test": "data"}

async def test_structured_logging(test_log_file, test_settings):
    """Test structured logging with complex data."""
    setup_logging(test_settings)
    logger = get_logger("test_structured")
    
    # Complex nested structure
    test_data = {
        "user": {
            "id": "user123",
            "roles": ["admin", "trader"]
        },
        "action": {
            "type": "trade",
            "details": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "amount": 1.0
            }
        },
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0"
        }
    }
    
    logger.info("Structured log test", extra=test_data)
    
    # Verify structured data
    with open(test_log_file) as f:
        log_data = json.loads(f.readline())
    
    assert log_data["user"]["id"] == test_data["user"]["id"]
    assert log_data["action"]["details"]["symbol"] == "BTCUSDT"
    assert "metadata" in log_data

async def test_log_rotation(tmp_path, test_settings):
    """Test log file rotation."""
    # Configure smaller max file size for testing
    log_file = tmp_path / "rotating.log"
    test_settings.MAX_LOG_SIZE = 1024  # 1KB
    test_settings.LOG_FILE_PATH = str(log_file)
    
    setup_logging(test_settings)
    logger = get_logger("test_rotation")
    
    # Write enough logs to trigger rotation
    large_message = "x" * 100
    for _ in range(20):
        logger.info(large_message)
    
    # Check for rotated files
    log_files = list(tmp_path.glob("rotating.log*"))
    assert len(log_files) > 1

async def test_concurrent_logging(test_log_file, test_settings):
    """Test logging from concurrent operations."""
    import asyncio
    
    setup_logging(test_settings)
    logger = get_logger("test_concurrent")
    
    async def log_operation(operation_id: int):
        for i in range(5):
            logger.info(
                f"Operation {operation_id} - Log {i}",
                extra={"operation_id": operation_id, "sequence": i}
            )
            await asyncio.sleep(0.1)
    
    # Run multiple logging operations concurrently
    await asyncio.gather(*[
        log_operation(i) for i in range(3)
    ])
    
    # Verify logs
    with open(test_log_file) as f:
        logs = f.readlines()
    
    assert len(logs) == 15  # 3 operations * 5 logs each
    
    # Verify log integrity
    for log in logs:
        log_data = json.loads(log)
        assert "operation_id" in log_data
        assert "sequence" in log_data

async def test_sensitive_data_handling(test_log_file, test_settings):
    """Test handling of sensitive data in logs."""
    setup_logging(test_settings)
    logger = get_logger("test_sensitive")
    
    sensitive_data = {
        "username": "test_user",
        "password": "secret123",  # Should not be logged
        "api_key": "api_secret",  # Should not be logged
        "public_data": "public"
    }
    
    logger.info(
        "User action",
        extra={
            "user": sensitive_data["username"],
            "action": "login",
            "public_data": sensitive_data["public_data"]
        }
    )
    
    # Verify sensitive data handling
    with open(test_log_file) as f:
        log_data = json.loads(f.readline())
    
    assert "password" not in log_data
    assert "api_key" not in log_data
    assert log_data["user"] == "test_user"
    assert log_data["public_data"] == "public"

async def test_error_context_logging(test_log_file, test_settings):
    """Test logging of error context and stack traces."""
    setup_logging(test_settings)
    logger = get_logger("test_error_context")
    
    def nested_function():
        raise ValueError("Nested error")
    
    try:
        try:
            nested_function()
        except ValueError as e:
            raise RuntimeError("Outer error") from e
    except Exception as e:
        logger.error(
            "Error in process",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "has_cause": bool(e.__cause__)
            },
            exc_info=True
        )
    
    # Verify error context
    with open(test_log_file) as f:
        log_data = json.loads(f.readline())
    
    assert log_data["error"] == "Outer error"
    assert log_data["error_type"] == "RuntimeError"
    assert log_data["has_cause"] is True
    assert "traceback" in log_data