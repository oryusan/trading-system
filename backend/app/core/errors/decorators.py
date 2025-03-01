"""
Core logging functionality with structured error handling.

This module provides logging services with:
- Structured JSON logging
- Error context enrichment
- Performance tracking
- Request tracing
- Optimized for performance with async handling
"""

import importlib
from typing import Dict, Any, Optional, List, Union, TYPE_CHECKING

# Type checking imports
if TYPE_CHECKING:
    from .logger import (
        get_logger, 
        init_logging, 
        cleanup_logging,
        configure_log_levels,
        LoggerProtocol,
        AsyncLogger
    )
    from .formatters import (
        create_formatter,
        JSONFormatter,
        TextFormatter,
        CompactFormatter,
        BaseLogFormatter
    )
else:
    # Lazy loading to prevent circular imports
    _logger_module = None
    _formatters_module = None
    
    def _get_logger_module():
        global _logger_module
        if _logger_module is None:
            _logger_module = importlib.import_module('.logger', package='app.core.logging')
        return _logger_module
    
    def _get_formatters_module():
        global _formatters_module
        if _formatters_module is None:
            _formatters_module = importlib.import_module('.formatters', package='app.core.logging')
        return _formatters_module
    
    # Function to get the logger with lazy loading
    def get_logger(name: str):
        return _get_logger_module().get_logger(name)
    
    # Other module functions
    def init_logging():
        return _get_logger_module().init_logging()
    
    def cleanup_logging():
        return _get_logger_module().cleanup_logging()
    
    def configure_log_levels(levels: Dict[str, Union[str, int]]):
        return _get_logger_module().configure_log_levels(levels)
    
    def create_formatter(fmt_type: str = "json", use_colors: bool = True, fmt_string: Optional[str] = None):
        return _get_formatters_module().create_formatter(fmt_type, use_colors, fmt_string)
    
    # Classes from the modules
    @property
    def LoggerProtocol():
        return _get_logger_module().LoggerProtocol
    
    @property
    def AsyncLogger():
        return _get_logger_module().AsyncLogger
    
    @property
    def BaseLogFormatter():
        return _get_formatters_module().BaseLogFormatter
    
    @property
    def JSONFormatter():
        return _get_formatters_module().JSONFormatter
    
    @property
    def TextFormatter():
        return _get_formatters_module().TextFormatter
    
    @property
    def CompactFormatter():
        return _get_formatters_module().CompactFormatter

__all__ = [
    # Main logging functions
    'get_logger',
    'init_logging', 
    'cleanup_logging',
    'configure_log_levels',
    'create_formatter',
    # Classes
    'LoggerProtocol',
    'AsyncLogger',
    'BaseLogFormatter',
    'JSONFormatter',
    'TextFormatter',
    'CompactFormatter'
]