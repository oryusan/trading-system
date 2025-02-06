"""
Configuration package initialization.

This module provides centralized access to application settings and constants.
Re-exports common configuration objects for convenient importing.
"""

from app.core.config.settings import settings
from app.core.config.constants import (
    trading_constants,
    system_constants,
    DateString,
    Numeric
)

__all__ = [
    'settings',
    'trading_constants', 
    'system_constants',
    'DateString',
    'Numeric'
]