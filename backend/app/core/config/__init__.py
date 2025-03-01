"""
Configuration package initialization.

This module provides centralized access to application settings and constants.
Optimized for lazy loading to improve startup performance.
"""

from typing import TYPE_CHECKING

# Use conditional imports to prevent circular dependencies
if TYPE_CHECKING:
    from .settings import settings
    from .constants import (
        trading_constants,
        system_constants,
        DateString,
        Numeric
    )
else:
    # Lazy imports for runtime
    from importlib import import_module
    
    # Import settings with a function to delay loading until accessed
    _settings_module = None
    
    def _get_settings():
        global _settings_module
        if _settings_module is None:
            _settings_module = import_module('.settings', package='app.core.config')
        return _settings_module.settings
    
    # Create a property-like object for settings
    class _LazySettings:
        def __getattr__(self, name):
            return getattr(_get_settings(), name)
        
        def __getitem__(self, key):
            return _get_settings()[key]
    
    settings = _LazySettings()
    
    # Import constants
    from .constants import (
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