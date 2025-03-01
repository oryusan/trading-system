"""
Authentication service package initialization.

This module aggregates authentication-related services and utilities:
- Authentication service for login/registration
- Password management for hashing/verification
- Token management for JWT operations
- Login tracking for rate limiting
"""

from .service import AuthenticationService, create_auth_service, auth_service
from .password import PasswordManager
from .tokens import TokenManager
from .tracking import LoginTracker, LoginAttemptInfo

__all__ = [
    # Services
    "auth_service",             # Global service instance
    "create_auth_service",      # Factory function
    "AuthenticationService",    # Service class
    
    # Manager classes
    "PasswordManager",          # Password operations
    "TokenManager",             # JWT token operations  
    "LoginTracker",             # Login attempt tracking
    "LoginAttemptInfo"          # Login attempt info model
]

# Async close function for application shutdown
async def close_auth_services() -> None:
    """Close all authentication service resources."""
    await auth_service.close()