"""Application-layer exceptions for the admin use-cases.

Domain exceptions live in app.domain.exceptions.*; this module adds
application-level concerns specific to the admin (bulk-add) flow.

RoleNotFoundError, RoleNotAllowedError, and PermissionDeniedError are
re-exported here so callers only import from one place.
"""

# Re-export from invitations for convenience (same exceptions, shared semantics)
from app.application.invitations.exceptions import (  # noqa: F401
    RoleNotFoundError,
    PermissionDeniedError,
)
from app.domain.exceptions.invitation_exceptions import (  # noqa: F401
    RoleNotAllowedError,
)


class TargetUserNotFoundError(Exception):
    """Raised when the target user to bulk-add does not exist. Maps to HTTP 404."""

    pass


class EmptyProjectListError(ValueError):
    """Raised when project_ids list is empty after deduplication. Maps to HTTP 400."""

    pass


class TooManyProjectsError(ValueError):
    """Raised when project_ids exceeds the allowed limit (50). Maps to HTTP 400.

    This is a defense-in-depth guard; Pydantic validation should catch it first
    at the route layer before the use-case is ever invoked.
    """

    pass
