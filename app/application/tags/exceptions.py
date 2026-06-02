"""Domain and application exceptions for the tags bounded context."""

from __future__ import annotations


class ProjectTagNotFoundError(Exception):
    """Raised when a requested project tag does not exist."""

    pass


class DuplicateProjectTagNameError(Exception):
    """Raised when a tag name already exists within the same project."""

    pass


class InvalidProjectTagError(ValueError):
    """Raised when a tag operation is invalid (e.g. cross-project assignment)."""

    pass


class NotProjectMemberError(Exception):
    """Raised when the acting user is not a member of the project."""

    pass
