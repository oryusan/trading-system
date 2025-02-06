# File: tests/unit/core/test_errors.py

import pytest
from fastapi import HTTPException
from datetime import datetime
from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    DatabaseError,
    ExchangeError,
    NotFoundError,
    ServiceError,
    RateLimitError,
    handle_api_error
)

async def test_authentication_error():
    """Test authentication error creation and context."""
    context = {
        "user_id": "test_user",
        "timestamp": datetime.utcnow()
    }
    
    error = AuthenticationError(
        "Invalid credentials",
        context=context
    )
    
    assert error.detail == "Invalid credentials"
    assert error.error_code == "AUTHENTICATION_ERROR"
    assert "user_id" in error.context
    assert "timestamp" in error.context
    assert error.status_code == 401

async def test_authorization_error():
    """Test authorization error with role context."""
    context = {
        "required_role": "admin",
        "user_role": "viewer"
    }
    
    error = AuthorizationError(
        "Insufficient permissions",
        context=context
    )
    
    assert error.detail == "Insufficient permissions"
    assert error.error_code == "AUTHORIZATION_ERROR"
    assert error.context["required_role"] == "admin"
    assert error.status_code == 403

async def test_validation_error():
    """Test validation error with field context."""
    context = {
        "field": "username",
        "value": "",
        "constraint": "non_empty"
    }
    
    error = ValidationError(
        "Username cannot be empty",
        context=context
    )
    
    assert "Username cannot be empty" in str(error)
    assert error.error_code == "VALIDATION_ERROR"
    assert error.context["field"] == "username"
    assert error.status_code == 422

async def test_database_error():
    """Test database error handling."""
    context = {
        "operation": "insert",
        "collection": "users",
        "error": "duplicate key"
    }
    
    error = DatabaseError(
        "Database operation failed",
        context=context
    )
    
    assert error.error_code == "DATABASE_ERROR"
    assert "operation" in error.context
    assert error.status_code == 500

async def test_exchange_error():
    """Test exchange-specific error handling."""
    context = {
        "exchange": "bybit",
        "operation": "place_order",
        "symbol": "BTCUSDT"
    }
    
    error = ExchangeError(
        "Order placement failed",
        context=context,
        exchange="bybit"
    )
    
    assert error.error_code == "EXCHANGE_ERROR"
    assert error.exchange == "bybit"
    assert "operation" in error.context
    assert error.status_code == 502

async def test_not_found_error():
    """Test not found error with resource context."""
    context = {
        "resource_type": "account",
        "resource_id": "123"
    }
    
    error = NotFoundError(
        "Account not found",
        context=context
    )
    
    assert error.error_code == "NOT_FOUND"
    assert error.context["resource_type"] == "account"
    assert error.status_code == 404

async def test_service_error():
    """Test service error with component context."""
    context = {
        "service": "telegram_bot",
        "operation": "send_message"
    }
    
    error = ServiceError(
        "Failed to send notification",
        context=context
    )
    
    assert error.error_code == "SERVICE_ERROR"
    assert "service" in error.context
    assert error.status_code == 503

async def test_rate_limit_error():
    """Test rate limit error with limit context."""
    context = {
        "limit": 5,
        "interval": "minute",
        "current_count": 6
    }
    
    error = RateLimitError(
        "Too many requests",
        context=context
    )
    
    assert error.error_code == "RATE_LIMIT_ERROR"
    assert error.context["limit"] == 5
    assert error.status_code == 429

async def test_error_handling_chain():
    """Test error handling chain and context propagation."""
    
    # Simulate a chain of errors (database → service → API)
    try:
        try:
            # Simulate database error
            raise DatabaseError(
                "Database connection failed",
                context={"db": "mongodb"}
            )
        except DatabaseError as db_error:
            # Add service context and re-raise
            raise ServiceError(
                "User service failed",
                context={
                    "service": "user_service",
                    "original_error": str(db_error)
                }
            ) from db_error
            
    except Exception as e:
        # Handle the error chain
        final_error = await handle_api_error(
            error=e,
            context={"api_endpoint": "/users"},
            log_message="API request failed"
        )
        
        assert isinstance(final_error, HTTPException)
        assert final_error.status_code == 503
        assert "original_error" in final_error.detail

async def test_error_retry_handling():
    """Test error handling with retry logic."""
    retry_count = 0
    
    async def operation_with_retries():
        nonlocal retry_count
        retry_count += 1
        if retry_count < 3:
            raise DatabaseError("Temporary failure")
        return "success"
    
    # Test retry logic
    try:
        result = await operation_with_retries()
        while retry_count < 3:
            try:
                result = await operation_with_retries()
                break
            except DatabaseError:
                continue
        
        assert result == "success"
        assert retry_count == 3
        
    except DatabaseError as e:
        assert False, "Should have succeeded after retries"

async def test_error_context_enrichment():
    """Test error context enrichment through handling chain."""
    
    base_context = {"user_id": "test_user"}
    
    try:
        raise ValidationError(
            "Initial validation error",
            context=base_context
        )
    except Exception as e:
        enriched_error = await handle_api_error(
            error=e,
            context={"request_id": "123", "endpoint": "/api/test"},
            error_class=ValidationError,
            log_message="Validation failed"
        )
        
        assert isinstance(enriched_error, HTTPException)
        error_detail = enriched_error.detail
        
        # Verify context enrichment
        assert "user_id" in str(error_detail)
        assert "request_id" in str(error_detail)
        assert "endpoint" in str(error_detail)

async def test_error_recovery_strategies():
    """Test different error recovery strategies."""
    
    async def test_recovery(error_type, max_retries=3):
        retries = 0
        
        while retries < max_retries:
            try:
                if retries < max_retries - 1:
                    if error_type == "rate_limit":
                        raise RateLimitError("Rate limit exceeded")
                    elif error_type == "database":
                        raise DatabaseError("Database connection failed")
                    else:
                        raise ServiceError("Generic service error")
                return "recovered"
            except Exception as e:
                retries += 1
                if retries == max_retries:
                    raise
                continue
        
    # Test rate limit recovery
    result = await test_recovery("rate_limit")
    assert result == "recovered"
    
    # Test database recovery
    result = await test_recovery("database")
    assert result == "recovered"
    
    # Test max retries exceeded
    with pytest.raises(ServiceError):
        await test_recovery("service", max_retries=1)

async def test_telegram_notification_error():
    """Test error handling for Telegram notifications."""
    context = {
        "chat_id": "test_chat",
        "message_type": "alert"
    }
    
    error = ServiceError(
        "Failed to send Telegram notification",
        context=context
    )
    
    assert error.error_code == "SERVICE_ERROR"
    assert "chat_id" in error.context
    assert error.status_code == 503

async def test_exchange_specific_errors():
    """Test exchange-specific error handling for different exchanges."""
    exchanges = ["bybit", "okx", "bitget"]
    
    for exchange in exchanges:
        context = {
            "exchange": exchange,
            "operation": "get_position",
            "symbol": "BTCUSDT"
        }
        
        error = ExchangeError(
            f"{exchange} API error",
            context=context,
            exchange=exchange
        )
        
        assert error.exchange == exchange
        assert error.context["exchange"] == exchange
        assert error.status_code == 502