"""Domain exceptions for authentication and authorization."""

from app.domain.exceptions.auth_exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    UserNotFoundError,
    UserInactiveError,
    AuthorizationError,
    InsufficientPermissionsError,
    RoleNotFoundError,
)

__all__ = [
    "AuthenticationError",
    "InvalidCredentialsError",
    "UserNotFoundError",
    "UserInactiveError",
    "AuthorizationError",
    "InsufficientPermissionsError",
    "RoleNotFoundError",
]
