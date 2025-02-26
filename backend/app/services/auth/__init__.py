"""
Authentication service package initialization.

This module aggregates authentication-related services and utilities:
- Authentication service for login/registration
- Password management for hashing/verification
- Token management for JWT operations
- Login tracking for rate limiting
"""

from .service import auth_service
from .password import password_manager
from .tokens import token_manager
from .tracking import login_tracker

__all__ = [
    "auth_service",      # Main authentication service
    "password_manager",  # Password operations
    "token_manager",     # JWT token operations  
    "login_tracker"      # Login attempt tracking
]