"""
Error handling implementation with recovery strategies.

Features:
- Error handler implementation
- Recovery strategy execution
- Error context enrichment
- Rate limiting and backoff
- Error logging integration
"""

from typing import Optional, Dict, Any, Type, Callable, Awaitable
import asyncio
import random
from datetime import datetime, timedelta

from app.core.errors.base import BaseError, ErrorLevel, ErrorCategory
from app.core.errors.types import (
    RecoveryStrategy,
    RecoveryConfig,
    NotificationConfig,
    DEFAULT_STRATEGIES,
    ErrorContext
)
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

class ErrorHandler:
    """
    Handles errors with appropriate recovery strategies.
    
    Features:
    - Error classification
    - Recovery strategy execution
    - Rate limiting
    - Error notification
    """
    
    def __init__(self):
        self.error_counts: Dict[str, int] = {}
        self.last_notification: Dict[str, datetime] = {}
        self.recovery_locks: Dict[str, asyncio.Lock] = {}

    async def handle_error(
        self,
        error: Exception,
        context: Optional[ErrorContext] = None,
        error_class: Optional[Type[BaseError]] = None,
        notification_override: Optional[bool] = None
    ) -> None:
        """
        Handle an error with context enrichment and recovery.
        
        Args:
            error: The error to handle
            context: Optional error context
            error_class: Optional specific error class to use
            notification_override: Override notification settings
        """
        try:
            # Convert to BaseError if needed
            if not isinstance(error, BaseError):
                if error_class:
                    error = error_class(str(error), context)
                else:
                    error = BaseError(str(error), context)

            # Enrich context
            self._enrich_error_context(error)
            
            # Track error
            self._track_error(error)

            # Get recovery strategy
            strategy = self._get_recovery_strategy(error)

            # Execute recovery if available
            if strategy:
                await self._execute_recovery(error, strategy)

            # Check notification
            should_notify = (
                notification_override if notification_override is not None
                else self._should_notify(error)
            )

            if should_notify:
                await self._send_notification(error)

            # Log error
            logger.error(
                str(error),
                extra={
                    "error_type": error.__class__.__name__,
                    "error_context": error.context,
                    "recovery_strategy": strategy.value if strategy else None
                }
            )

        except Exception as e:
            logger.error(
                "Error handler failed",
                extra={
                    "original_error": str(error),
                    "handler_error": str(e)
                }
            )

    def _enrich_error_context(self, error: BaseError) -> None:
        """Enrich error context with additional information."""
        if not error.context:
            error.context = {}
            
        error.context.update({
            "timestamp": datetime.utcnow().isoformat(),
            "error_count": self.error_counts.get(
                error.__class__.__name__, 0
            ),
        })

        # Add recovery attempt info if exists
        if hasattr(error, "recovery_attempts"):
            error.context["recovery_attempts"] = error.recovery_attempts

    def _track_error(self, error: BaseError) -> None:
        """Track error occurrence."""
        error_type = error.__class__.__name__
        self.error_counts[error_type] = (
            self.error_counts.get(error_type, 0) + 1
        )

    def _get_recovery_strategy(
        self,
        error: BaseError
    ) -> Optional[RecoveryStrategy]:
        """Get appropriate recovery strategy for error."""
        # Check default strategies first
        if error.category in DEFAULT_STRATEGIES:
            return DEFAULT_STRATEGIES[error.category]

        # Custom logic based on error specifics
        if error.level in [ErrorLevel.LOW, ErrorLevel.MEDIUM]:
            return RecoveryStrategy.RETRY

        if error.level == ErrorLevel.HIGH:
            return RecoveryStrategy.RETRY_WITH_BACKOFF

        return None  # No recovery for CRITICAL

    async def _execute_recovery(
        self,
        error: BaseError,
        strategy: RecoveryStrategy
    ) -> None:
        """
        Execute recovery strategy with rate limiting.
        
        Args:
            error: Error to recover from
            strategy: Strategy to execute
        """
        # Get or create lock
        error_key = f"{error.__class__.__name__}_{id(error)}"
        if error_key not in self.recovery_locks:
            self.recovery_locks[error_key] = asyncio.Lock()

        async with self.recovery_locks[error_key]:
            try:
                if strategy == RecoveryStrategy.RETRY:
                    await self._retry_operation(error)
                    
                elif strategy == RecoveryStrategy.WAIT_AND_RETRY:
                    await self._wait_and_retry(error)
                    
                elif strategy == RecoveryStrategy.RETRY_WITH_BACKOFF:
                    await self._retry_with_backoff(error)
                    
                elif strategy == RecoveryStrategy.RECONNECT:
                    await self._handle_reconnection(error)
                    
                elif strategy == RecoveryStrategy.CANCEL_AND_RETRY:
                    await self._cancel_and_retry(error)
                    
                elif strategy == RecoveryStrategy.CLOSE_AND_RESET:
                    await self._close_and_reset(error)

            except Exception as e:
                logger.error(
                    "Recovery failed",
                    extra={
                        "error": str(error),
                        "strategy": strategy.value,
                        "recovery_error": str(e)
                    }
                )
            finally:
                # Cleanup lock if needed
                if error_key in self.recovery_locks:
                    del self.recovery_locks[error_key]

    async def _retry_operation(self, error: BaseError) -> None:
        """Simple retry without delay."""
        for attempt in range(RecoveryConfig.MAX_RETRIES):
            try:
                if hasattr(error, "retry_operation"):
                    await error.retry_operation()
                    return
            except Exception:
                if attempt == RecoveryConfig.MAX_RETRIES - 1:
                    raise

    async def _wait_and_retry(self, error: BaseError) -> None:
        """Retry with fixed delay."""
        for attempt in range(RecoveryConfig.MAX_RETRIES):
            try:
                await asyncio.sleep(RecoveryConfig.RETRY_DELAY)
                if hasattr(error, "retry_operation"):
                    await error.retry_operation()
                    return
            except Exception:
                if attempt == RecoveryConfig.MAX_RETRIES - 1:
                    raise

    async def _retry_with_backoff(self, error: BaseError) -> None:
        """Retry with exponential backoff."""
        for attempt in range(RecoveryConfig.MAX_RETRIES):
            try:
                base_delay = RecoveryConfig.RETRY_DELAY * (RecoveryConfig.BACKOFF_FACTOR ** attempt)
                delay = random.uniform(base_delay / 2, base_delay)  # Random jitter
                delay = min(delay, RecoveryConfig.MAX_DELAY)
                
                await asyncio.sleep(delay)
                
                if hasattr(error, "retry_operation"):
                    await error.retry_operation()
                    return
            except Exception:
                if attempt == RecoveryConfig.MAX_RETRIES - 1:
                    raise

    async def _handle_reconnection(self, error: BaseError) -> None:
        """Handle reconnection for network/websocket errors."""
        if hasattr(error, "reconnect"):
            await error.reconnect()

    async def _cancel_and_retry(self, error: BaseError) -> None:
        """Cancel current operation and retry."""
        if hasattr(error, "cancel_operation"):
            await error.cancel_operation()
            await self._retry_operation(error)

    async def _close_and_reset(self, error: BaseError) -> None:
        """Close connections and reset state."""
        if hasattr(error, "close_and_reset"):
            await error.close_and_reset()

    def _should_notify(self, error: BaseError) -> bool:
        """Check if error should trigger notification."""
        # Check notification levels
        if error.level not in NotificationConfig.NOTIFY_LEVELS:
            return False
            
        # Check cooldown
        error_type = error.__class__.__name__
        last_time = self.last_notification.get(error_type)
        
        if last_time and (
            datetime.utcnow() - last_time
        ).total_seconds() < NotificationConfig.COOLDOWN_PERIOD:
            return False
            
        # Update notification time
        self.last_notification[error_type] = datetime.utcnow()
        return True

    async def _send_notification(self, error: BaseError) -> None:
        """Send error notification."""
        try:
            # Get notification service
            from app.services.telegram.service import telegram_bot
            
            # Format message
            message = (
                f"Error: {error.__class__.__name__}\n"
                f"Message: {str(error)}\n"
                f"Level: {error.level.value}\n"
                f"Category: {error.category.value}\n"
                f"Context: {error.context}"
            )
            
            # Send notification
            await telegram_bot.send_error_notification(message)
            
        except Exception as e:
            logger.error(
                "Failed to send error notification",
                extra={
                    "error": str(error),
                    "notification_error": str(e)
                }
            )

# Global error handler instance
error_handler = ErrorHandler()

async def handle_api_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    log_message: Optional[str] = None
) -> None:
    """
    Handle API endpoint errors with context enrichment.
    
    Args:
        error: The error to handle
        context: Optional error context
        log_message: Optional log message
    """
    await error_handler.handle_error(
        error=error,
        context=context,
        notification_override=True
    )

    if log_message:
        logger.error(
            log_message,
            extra={
                "error": str(error),
                "context": context
            }
        )