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
from app.domain.exceptions.project_exceptions import (
    ProjectError,
    ProjectNotFoundError,
    ProjectAccessDeniedError,
    InvalidProjectDataError,
)

__all__ = [
    "AuthenticationError",
    "InvalidCredentialsError",
    "UserNotFoundError",
    "UserInactiveError",
    "AuthorizationError",
    "InsufficientPermissionsError",
    "RoleNotFoundError",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectAccessDeniedError",
    "InvalidProjectDataError",
]
