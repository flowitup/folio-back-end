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
from app.domain.exceptions.labor_exceptions import (
    LaborError,
    WorkerNotFoundError,
    LaborEntryNotFoundError,
    DuplicateEntryError,
    InvalidWorkerDataError,
    InvalidLaborEntryError,
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
    "LaborError",
    "WorkerNotFoundError",
    "LaborEntryNotFoundError",
    "DuplicateEntryError",
    "InvalidWorkerDataError",
    "InvalidLaborEntryError",
]
