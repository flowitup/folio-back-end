"""Use-case-level exceptions for the project analyses application layer."""

from __future__ import annotations


class AnalysisNotFoundError(Exception):
    """Raised when a requested analysis does not exist, is soft-deleted, or
    belongs to a different project than the one in the request URL."""

    pass


class InvalidAnalysisFileError(Exception):
    """Raised when an uploaded file fails extension/mimetype/UTF-8 validation."""

    pass


class AnalysisTooLargeError(Exception):
    """Raised when an uploaded file exceeds the configured size cap."""

    pass


# ``PermissionDeniedError`` and ``NotProjectMemberError`` are intentionally NOT
# redefined here — re-export the shared exceptions already used by the
# invitations and notes modules so callers catch one canonical type per
# concern instead of a per-module fork.
from app.application.invitations.exceptions import PermissionDeniedError as PermissionDeniedError  # noqa: E402,F401
from app.application.notes.exceptions import NotProjectMemberError as NotProjectMemberError  # noqa: E402,F401
