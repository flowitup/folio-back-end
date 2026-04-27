"""Application-layer exceptions for the invitations use-cases.

Domain exceptions (expired/revoked/accepted/token) live in
app.domain.exceptions.invitation_exceptions and are re-raised as-is.
This module adds application-level concerns: permissions, rate limiting,
and missing-resource errors specific to the invitation flow.
"""

from app.domain.exceptions.project_exceptions import ProjectNotFoundError  # noqa: F401 – re-export


class PermissionDeniedError(Exception):
    """Raised when an actor lacks the required permission for an invitation operation."""

    pass


class RateLimitedError(Exception):
    """Raised when a project-level daily invitation cap is exceeded."""

    pass


class RoleNotFoundError(Exception):
    """Raised when the requested role does not exist."""

    pass
