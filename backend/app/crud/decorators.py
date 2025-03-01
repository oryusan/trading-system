"""
This module provides a decorator to wrap asynchronous CRUD methods
with uniform error handling. If any exception occurs, it will be caught
and re‑raised as a DatabaseError with additional context.
"""

from functools import wraps
from app.core.errors.base import DatabaseError

def handle_db_error(error_message: str, context_getter):
    """
    Decorator to handle database errors and wrap them into a DatabaseError
    with additional context.
    
    Args:
        error_message (str): The message to use when wrapping the error.
        context_getter (Callable): A function that extracts a context dict
            from the decorated function’s arguments.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context = context_getter(*args, **kwargs)
                context["error"] = str(e)
                raise DatabaseError(error_message, context=context) from e
        return wrapper
    return decorator
