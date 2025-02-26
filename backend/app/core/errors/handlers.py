import asyncio
import random
from datetime import datetime
from typing import Optional, Dict, Any, Type, Callable, Awaitable

from .base import BaseError
from .types import RECOVERY_CONFIG, NOTIFICATION_CONFIG, DEFAULT_STRATEGIES, ErrorContext
from app.core.enums import ErrorLevel, ErrorCategory, RecoveryStrategy
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class ErrorHandler:
    """
    Handles errors using recovery strategies, notifications, and logging.
    """

    def __init__(self) -> None:
        self.error_counts: Dict[str, int] = {}
        self.last_notification: Dict[str, datetime] = {}
        self.recovery_locks: Dict[str, asyncio.Lock] = {}

    async def handle_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        error_class: Optional[Type[BaseError]] = None,
        notification_override: Optional[bool] = None,
    ) -> None:
        """
        Process an error by enriching its context, tracking its occurrence,
        executing a recovery strategy if applicable, and sending notifications.
        """
        try:
            # Convert error to a BaseError if needed
            if not isinstance(error, BaseError):
                error = error_class(str(error), context) if error_class else BaseError(str(error), context)
            self._enrich_error_context(error)
            self._track_error(error)

            strategy = self._get_recovery_strategy(error)
            if strategy:
                await self._execute_recovery(error, strategy)

            should_notify = (
                notification_override
                if notification_override is not None
                else self._should_notify(error)
            )
            if should_notify:
                await self._send_notification(error)

            logger.error(
                str(error),
                extra={
                    "error_type": error.__class__.__name__,
                    "error_context": error.context,
                    "recovery_strategy": strategy.value if strategy else None,
                },
            )
        except Exception as e:
            logger.error(
                "Error handler failed",
                extra={"original_error": str(error), "handler_error": str(e)},
            )

    def _enrich_error_context(self, error: BaseError) -> None:
        """Enrich the error context with a timestamp and error count."""
        error.context = error.context or {}
        error.context.update({
            "timestamp": datetime.utcnow().isoformat(),
            "error_count": self.error_counts.get(error.__class__.__name__, 0),
        })
        if hasattr(error, "recovery_attempts"):
            error.context["recovery_attempts"] = error.recovery_attempts

    def _track_error(self, error: BaseError) -> None:
        """Increment the error count for the given error type."""
        error_type = error.__class__.__name__
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

    def _get_recovery_strategy(self, error: BaseError) -> Optional[RecoveryStrategy]:
        """
        Determine the appropriate recovery strategy based on the error's category
        or level.
        """
        if error.category in DEFAULT_STRATEGIES:
            return DEFAULT_STRATEGIES[error.category]
        if error.level in (ErrorLevel.LOW, ErrorLevel.MEDIUM):
            return RecoveryStrategy.RETRY
        if error.level == ErrorLevel.HIGH:
            return RecoveryStrategy.RETRY_WITH_BACKOFF
        return None

    async def _execute_recovery(self, error: BaseError, strategy: RecoveryStrategy) -> None:
        """
        Execute the given recovery strategy using a lock to avoid
        concurrent recovery attempts.
        """
        error_key = f"{error.__class__.__name__}_{id(error)}"
        lock = self.recovery_locks.setdefault(error_key, asyncio.Lock())

        async with lock:
            strategy_actions: Dict[RecoveryStrategy, Callable[[BaseError], Awaitable[None]]] = {
                RecoveryStrategy.RETRY: self._retry_operation,
                RecoveryStrategy.WAIT_AND_RETRY: self._wait_and_retry,
                RecoveryStrategy.RETRY_WITH_BACKOFF: self._retry_with_backoff,
                RecoveryStrategy.RECONNECT: self._handle_reconnection,
                RecoveryStrategy.CANCEL_AND_RETRY: self._cancel_and_retry,
                RecoveryStrategy.CLOSE_AND_RESET: self._close_and_reset,
            }
            action = strategy_actions.get(strategy)
            if action:
                try:
                    await action(error)
                except Exception as e:
                    logger.error(
                        "Recovery failed",
                        extra={
                            "error": str(error),
                            "strategy": strategy.value,
                            "recovery_error": str(e),
                        },
                    )
            self.recovery_locks.pop(error_key, None)

    async def _attempt_operation(
        self,
        error: BaseError,
        delay_func: Callable[[int], float],
        sleep_before_attempt: bool,
    ) -> None:
        """
        Generic retry helper that attempts to run error.retry_operation() up to
        RECOVERY_CONFIG.max_retries times, waiting for a delay determined by delay_func.
        
        Args:
            error: The error containing the retry_operation method.
            delay_func: A function that computes the delay based on the attempt count.
            sleep_before_attempt: If True, always sleep before each retry (even the first).
        """
        for attempt in range(RECOVERY_CONFIG.max_retries):
            try:
                if sleep_before_attempt:
                    await asyncio.sleep(delay_func(attempt))
                if hasattr(error, "retry_operation"):
                    await error.retry_operation()
                    return
            except Exception:
                if attempt == RECOVERY_CONFIG.max_retries - 1:
                    raise

    async def _retry_operation(self, error: BaseError) -> None:
        """Perform a simple retry without any delay."""
        await self._attempt_operation(error, lambda attempt: 0, sleep_before_attempt=False)

    async def _wait_and_retry(self, error: BaseError) -> None:
        """Retry with a fixed delay between attempts."""
        await self._attempt_operation(
            error,
            lambda attempt: RECOVERY_CONFIG.retry_delay,
            sleep_before_attempt=True,
        )

    async def _retry_with_backoff(self, error: BaseError) -> None:
        """Retry using an exponential backoff with randomness."""
        def backoff_delay(attempt: int) -> float:
            base_delay = RECOVERY_CONFIG.retry_delay * (RECOVERY_CONFIG.backoff_factor ** attempt)
            delay = random.uniform(base_delay / 2, base_delay)
            return min(delay, RECOVERY_CONFIG.max_delay)
        await self._attempt_operation(error, backoff_delay, sleep_before_attempt=True)

    async def _handle_reconnection(self, error: BaseError) -> None:
        """Attempt to reconnect if the error supports a reconnection operation."""
        if hasattr(error, "reconnect"):
            await error.reconnect()

    async def _cancel_and_retry(self, error: BaseError) -> None:
        """Cancel the current operation and then retry."""
        if hasattr(error, "cancel_operation"):
            await error.cancel_operation()
            await self._retry_operation(error)

    async def _close_and_reset(self, error: BaseError) -> None:
        """Close any open connections and reset state if supported."""
        if hasattr(error, "close_and_reset"):
            await error.close_and_reset()

    def _should_notify(self, error: BaseError) -> bool:
        """
        Determine if a notification should be sent based on the error's level and
        the time elapsed since the last notification.
        """
        if error.level not in NOTIFICATION_CONFIG.notify_levels:
            return False

        error_type = error.__class__.__name__
        last_time = self.last_notification.get(error_type)
        if last_time and (datetime.utcnow() - last_time).total_seconds() < NOTIFICATION_CONFIG.cooldown_period:
            return False

        self.last_notification[error_type] = datetime.utcnow()
        return True

    async def _send_notification(self, error: BaseError) -> None:
        """Send an error notification (e.g. via a Telegram bot)."""
        try:
            from app.services.telegram.service import telegram_bot

            message = (
                f"Error: {error.__class__.__name__}\n"
                f"Message: {str(error)}\n"
                f"Level: {getattr(error.level, 'value', error.level)}\n"
                f"Category: {getattr(error.category, 'value', error.category)}\n"
                f"Context: {error.context}"
            )
            await telegram_bot.send_error_notification(message)
        except Exception as e:
            logger.error(
                "Failed to send error notification",
                extra={"error": str(error), "notification_error": str(e)},
            )


# Global error handler instance
error_handler = ErrorHandler()


async def handle_api_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    log_message: Optional[str] = None,
) -> None:
    """
    Handle API errors by processing them and logging additional details if provided.
    
    Args:
        error: The error to process.
        context: Additional context for the error.
        log_message: An optional message to accompany the error log.
    """
    await error_handler.handle_error(error=error, context=context, notification_override=True)
    if log_message:
        logger.error(log_message, extra={"error": str(error), "context": context})
