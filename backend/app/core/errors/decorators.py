import asyncio
from functools import wraps
from typing import Any, Callable, Dict, Optional

from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

def error_handler(
    *args: Any,
    context_extractor: Optional[Callable[..., Dict[str, Any]]] = None,
    log_message: Optional[str] = None,
    log_args: bool = False
) -> Callable:
    """
    Decorator for centralized error handling.
    
    Usage:
      - With a tag:
          @error_handler("hash_password", log_message="Error hashing password")
          async def hash_password(...): ...
      - With an explicit context extractor:
          @error_handler(context_extractor=lambda *a, **kw: {"user_id": kw.get("user_id")},
                         log_message="Error in service method", log_args=True)
          async def some_service_method(...): ...
    
    If a positional argument is given, it will be used as a tag (unless a context_extractor is provided).
    In that case, if no context_extractor is provided, a default lambda is used to add {"tag": <tag>}.
    """
    tag = args[0] if args else None
    if tag and context_extractor is None:
        context_extractor = lambda *a, **kw: {"tag": tag}

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context: Dict[str, Any] = {}
                if context_extractor:
                    try:
                        context.update(context_extractor(*args, **kwargs))
                    except Exception as ce:
                        logger.error("Error in context_extractor", extra={"error": str(ce)})
                if log_args:
                    context["args"] = repr(args)
                    context["kwargs"] = repr(kwargs)
                msg = log_message if log_message else f"Error in function {func.__name__}"
                await handle_api_error(error=e, context=context, log_message=msg)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context: Dict[str, Any] = {}
                if context_extractor:
                    try:
                        context.update(context_extractor(*args, **kwargs))
                    except Exception as ce:
                        logger.error("Error in context_extractor", extra={"error": str(ce)})
                if log_args:
                    context["args"] = repr(args)
                    context["kwargs"] = repr(kwargs)
                msg = log_message if log_message else f"Error in function {func.__name__}"
                logger.error(msg, extra={"context": context, "error": str(e)})
                raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
