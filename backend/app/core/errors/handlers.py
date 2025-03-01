import asyncio
import random
import time
from datetime import datetime
from typing import Optional, Dict, Any, Type, Callable, Awaitable, Set, List
from weakref import WeakValueDictionary

from .base import BaseError
from .types import RECOVERY_CONFIG, NOTIFICATION_CONFIG, DEFAULT_STRATEGIES, ErrorContext, BatchError
from app.core.enums import ErrorLevel, ErrorCategory, RecoveryStrategy
from app.core.logging.logger import get_logger
from app.core.config import settings

logger = get_logger(__name__)


class ErrorHandler:
    """
    Handles errors using recovery strategies, notifications, and logging.
    Optimized for performance with lock cleanup, batching, and timeout prevention.
    """

    def __init__(self) -> None:
        """Initialize the error handler with optimized data structures."""
        self.error_counts: Dict[str, int] = {}
        self.last_notification: Dict[str, datetime] = {}
        
        # Use WeakValueDictionary to automatically cleanup locks when they're no longer referenced
        self.recovery_locks: WeakValueDictionary = WeakValueDictionary()
        
        # Timestamp-based lock tracking for manual cleanup
        self.lock_timestamps: Dict[str, float] = {}
        
        # Batch processing for similar errors
        self.error_batches: Dict[str, List[BatchError]] = {}
        self.batch_processing_lock = asyncio.Lock()
        
        # Start the cleanup task for stale locks
        self._start_lock_cleanup()
        
        # Start the batch processing task
        self._start_batch_processor()

    def _start_lock_cleanup(self) -> None:
        """Start a background task to clean up stale locks."""
        async def cleanup_locks() -> None:
            while True:
                try:
                    await asyncio.sleep(settings.error.ERROR_LOCK_CLEANUP_INTERVAL)
                    await self._cleanup_stale_locks()
                except Exception as e:
                    logger.error(f"Error in lock cleanup task: {e}")

        # Schedule the cleanup task but don't wait for it
        asyncio.create_task(cleanup_locks())

    def _start_batch_processor(self) -> None:
        """Start a background task to process error batches."""
        async def process_batches() -> None:
            while True:
                try:
                    await asyncio.sleep(settings.error.ERROR_BATCH_INTERVAL)
                    await self._process_error_batches()
                except Exception as e:
                    logger.error(f"Error in batch processing task: {e}")

        # Schedule the batch processing task
        asyncio.create_task(process_batches())

    async def _cleanup_stale_locks(self) -> None:
        """Remove locks that have been active for too long."""
        current_time = time.time()
        lock_max_age = settings.error.ERROR_LOCK_MAX_AGE
        
        # Identify keys to remove (stale locks)
        keys_to_remove: Set[str] = set()
        for key, timestamp in self.lock_timestamps.items():
            if current_time - timestamp > lock_max_age:
                keys_to_remove.add(key)
        
        # Remove stale locks
        for key in keys_to_remove:
            self.lock_timestamps.pop(key, None)
            # Note: the locks will be garbage collected if they're no longer referenced

        if keys_to_remove:
            logger.info(f"Cleaned up {len(keys_to_remove)} stale error recovery locks")

    async def _process_error_batches(self) -> None:
        """Process batched errors together for efficiency."""
        async with self.batch_processing_lock:
            for error_type, batch in self.error_batches.items():
                if not batch:
                    continue
                
                try:
                    # Process up to batch_size errors of this type
                    batch_size = settings.error.ERROR_BATCH_SIZE
                    to_process = batch[:batch_size]
                    
                    # Log as a group
                    logger.info(f"Processing batch of {len(to_process)} {error_type} errors")
                    
                    # Process each error
                    for batch_error in to_process:
                        await self._process_single_error(batch_error.error, 
                                                       batch_error.context, 
                                                       batch_error.error_class, 
                                                       batch_error.notification_override)
                    
                    # Remove processed errors from the batch
                    self.error_batches[error_type] = batch[batch_size:]
                except Exception as e:
                    logger.error(f"Error processing batch of {error_type}: {e}")

    async def handle_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        error_class: Optional[Type[BaseError]] = None,
        notification_override: Optional[bool] = None,
        batch_mode: bool = True,
    ) -> None:
        """
        Process an error by enriching its context, tracking its occurrence,
        executing a recovery strategy if applicable, and sending notifications.
        
        Args:
            error: The exception to handle
            context: Additional context for the error
            error_class: Optional specific error class to use
            notification_override: Force notification on/off
            batch_mode: Whether to process this error in a batch (default: True)
        """
        try:
            # Convert error to a BaseError if needed
            if not isinstance(error, BaseError):
                error = error_class(str(error), context) if error_class else BaseError(str(error), context)
            
            error_type = error.__class__.__name__
            
            # If batch mode is enabled, add to batch for later processing
            if batch_mode and not self._is_critical_error(error):
                if error_type not in self.error_batches:
                    self.error_batches[error_type] = []
                
                # Add to batch queue
                batch_error = BatchError(
                    error=error,
                    context=context,
                    error_class=error_class,
                    notification_override=notification_override
                )
                self.error_batches[error_type].append(batch_error)
                
                # Log that error was batched but don't process yet
                logger.debug(f"Batched {error_type} error: {str(error)}")
                return
            
            # Process immediately for critical errors or when batch_mode=False
            await self._process_single_error(error, context, error_class, notification_override)
            
        except Exception as e:
            logger.error(
                "Error handler failed",
                extra={"original_error": str(error), "handler_error": str(e)},
            )

    async def _process_single_error(
        self,
        error: BaseError,
        context: Optional[Dict[str, Any]] = None,
        error_class: Optional[Type[BaseError]] = None,
        notification_override: Optional[bool] = None,
    ) -> None:
        """Process a single error (internal method)."""
        try:
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
                "Error processing failed",
                extra={"original_error": str(error), "processing_error": str(e)},
            )

    def _is_critical_error(self, error: BaseError) -> bool:
        """Determine if an error is critical and should be processed immediately."""
        return error.level == ErrorLevel.CRITICAL

    def _enrich_error_context(self, error: BaseError) -> None:
        """Enrich the error context with a timestamp and error count."""
        error.context = error.context or {}
        error.context.update({
            "timestamp": datetime.utcnow().isoformat(),
            "error_count": self.error_counts.get(error.__class__.__name__, 0),
            "environment": settings.app.ENVIRONMENT.value,
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
        error_type = error.__class__.__name__
        
        # First try to get strategy from settings
        if settings.error.ERROR_RECOVERY_STRATEGIES:
            strategy = settings.error.get_error_recovery_strategy(error_type)
            if strategy:
                return strategy
                
        # Then fall back to default strategies by category
        if error.category in DEFAULT_STRATEGIES:
            return DEFAULT_STRATEGIES[error.category]
            
        # Finally fall back to strategies based on level
        if error.level in (ErrorLevel.LOW, ErrorLevel.MEDIUM):
            return RecoveryStrategy.RETRY
        if error.level == ErrorLevel.HIGH:
            return RecoveryStrategy.RETRY_WITH_BACKOFF
            
        return None

    async def _execute_recovery(
        self, error: BaseError, strategy: RecoveryStrategy, 
        lock_timeout: int = 30
    ) -> None:
        """
        Execute the given recovery strategy using a lock to avoid
        concurrent recovery attempts.
        
        Args:
            error: The error to recover from
            strategy: The recovery strategy to apply
            lock_timeout: Maximum time to wait for lock acquisition (seconds)
        """
        error_key = f"{error.__class__.__name__}_{id(error)}"
        
        # Create a new lock if needed
        if error_key not in self.recovery_locks:
            self.recovery_locks[error_key] = asyncio.Lock()
            self.lock_timestamps[error_key] = time.time()
        
        lock = self.recovery_locks[error_key]
        
        # Try to acquire the lock with timeout
        try:
            async with asyncio.timeout(lock_timeout):
                async with lock:
                    # Update timestamp when lock is acquired
                    self.lock_timestamps[error_key] = time.time()
                    
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
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for recovery lock on {error_key}")
        finally:
            # Clean up reference if we're the last user
            if error_key in self.recovery_locks and not lock.locked():
                self.lock_timestamps.pop(error_key, None)

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
        max_retries = settings.error.ERROR_RETRY_ATTEMPTS or RECOVERY_CONFIG.max_retries
        
        for attempt in range(max_retries):
            try:
                if sleep_before_attempt:
                    delay = delay_func(attempt)
                    error.context["retry_delay"] = delay
                    await asyncio.sleep(delay)
                    
                if hasattr(error, "retry_operation"):
                    error.context["retry_attempt"] = attempt + 1
                    await error.retry_operation()
                    error.context["retry_success"] = True
                    return
            except Exception as retry_error:
                error.context["retry_error"] = str(retry_error)
                if attempt == max_retries - 1:
                    error.context["retry_exhausted"] = True
                    raise

    async def _retry_operation(self, error: BaseError) -> None:
        """Perform a simple retry without any delay."""
        await self._attempt_operation(error, lambda attempt: 0, sleep_before_attempt=False)

    async def _wait_and_retry(self, error: BaseError) -> None:
        """Retry with a fixed delay between attempts."""
        retry_delay = settings.error.ERROR_RETRY_DELAY or RECOVERY_CONFIG.retry_delay
        await self._attempt_operation(
            error,
            lambda attempt: retry_delay,
            sleep_before_attempt=True,
        )

    async def _retry_with_backoff(self, error: BaseError) -> None:
        """Retry using an exponential backoff with randomness."""
        retry_delay = settings.error.ERROR_RETRY_DELAY or RECOVERY_CONFIG.retry_delay
        
        def backoff_delay(attempt: int) -> float:
            base_delay = retry_delay * (RECOVERY_CONFIG.backoff_factor ** attempt)
            jitter = random.uniform(0.75, 1.25)  # Add 25% jitter
            delay = base_delay * jitter
            return min(delay, RECOVERY_CONFIG.max_delay)
            
        await self._attempt_operation(error, backoff_delay, sleep_before_attempt=True)

    async def _handle_reconnection(self, error: BaseError) -> None:
        """Attempt to reconnect if the error supports a reconnection operation."""
        if hasattr(error, "reconnect"):
            error.context["reconnect_attempt"] = True
            try:
                await error.reconnect()
                error.context["reconnect_success"] = True
            except Exception as e:
                error.context["reconnect_error"] = str(e)
                raise

    async def _cancel_and_retry(self, error: BaseError) -> None:
        """Cancel the current operation and then retry."""
        if hasattr(error, "cancel_operation"):
            error.context["cancel_attempt"] = True
            try:
                await error.cancel_operation()
                error.context["cancel_success"] = True
                await self._retry_operation(error)
            except Exception as e:
                error.context["cancel_error"] = str(e)
                raise

    async def _close_and_reset(self, error: BaseError) -> None:
        """Close any open connections and reset state if supported."""
        if hasattr(error, "close_and_reset"):
            error.context["reset_attempt"] = True
            try:
                await error.close_and_reset()
                error.context["reset_success"] = True
            except Exception as e:
                error.context["reset_error"] = str(e)
                raise

    def _should_notify(self, error: BaseError) -> bool:
        """
        Determine if a notification should be sent based on the error's level and
        the time elapsed since the last notification.
        """
        # First check if this level should be notified at all
        notify_levels = settings.get_notification_config().get("levels", 
                                                             NOTIFICATION_CONFIG.notify_levels)
        if error.level not in notify_levels:
            return False

        # Check for cooldown period
        error_type = error.__class__.__name__
        last_time = self.last_notification.get(error_type)
        cooldown = settings.error.ERROR_NOTIFICATION_COOLDOWN or NOTIFICATION_CONFIG.cooldown_period
        
        if last_time and (datetime.utcnow() - last_time).total_seconds() < cooldown:
            return False

        # Update last notification time
        self.last_notification[error_type] = datetime.utcnow()
        return True

    async def _send_notification(self, error: BaseError) -> None:
        """Send an error notification (e.g. via a Telegram bot)."""
        try:
            from app.services.telegram.service import telegram_bot

            # Create a formatted message with critical information
            message = (
                f"🚨 Error Alert 🚨\n"
                f"Type: {error.__class__.__name__}\n"
                f"Message: {str(error)}\n"
                f"Level: {getattr(error.level, 'value', error.level)}\n"
                f"Category: {getattr(error.category, 'value', error.category)}\n"
                f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Environment: {settings.app.ENVIRONMENT.value}\n"
            )
            
            # Add context information
            if error.context:
                # Limit context to key fields to avoid message size issues
                important_keys = ['timestamp', 'retry_attempt', 'operation', 'service']
                context_str = "\n".join(
                    f"- {k}: {v}" 
                    for k, v in error.context.items() 
                    if k in important_keys or len(important_keys) < 5
                )
                message += f"\nContext:\n{context_str}"
            
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
    batch_mode: bool = False,
) -> None:
    """
    Handle API errors by processing them and logging additional details if provided.
    
    Args:
        error: The error to process.
        context: Additional context for the error.
        log_message: An optional message to accompany the error log.
        batch_mode: Whether to process errors in batch mode.
    """
    # Add request path to context if available
    if context and 'request' in context and hasattr(context['request'], 'url'):
        if not context.get('path'):
            context['path'] = str(context['request'].url)
            
    # Request context merging
    if context and log_message:
        context['log_message'] = log_message
        
    # Process the error (batching for non-critical errors)
    await error_handler.handle_error(
        error=error, 
        context=context, 
        notification_override=True,
        batch_mode=batch_mode
    )
    
    # Always log the message immediately regardless of batching
    if log_message:
        logger.error(log_message, extra={"error": str(error), "context": context})