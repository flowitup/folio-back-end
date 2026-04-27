"""Domain and application exceptions for the notes module."""

from __future__ import annotations


class NoteNotFoundError(Exception):
    """Raised when a requested note does not exist."""

    pass


class NotProjectMemberError(Exception):
    """Raised when the acting user is not a member of the note's project."""

    pass


class InvalidLeadTimeError(ValueError):
    """Raised when lead_time_minutes is not one of {0, 60, 1440}."""

    pass
