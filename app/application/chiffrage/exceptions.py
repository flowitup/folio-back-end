"""Application exceptions for the chiffrage module."""

from __future__ import annotations


class PosteNotFoundError(Exception):
    """Raised when a requested poste does not exist."""


class ArticleNotFoundError(Exception):
    """Raised when a requested article does not exist."""


class QuoteNotFoundError(Exception):
    """Raised when a requested quote does not exist."""


class UnitNotFoundError(Exception):
    """Raised when a requested custom unit does not exist."""


class InvalidChiffrageInputError(ValueError):
    """Raised when input fails a domain rule (blank name, unknown unit, no supplier)."""


class UnitAlreadyExistsError(Exception):
    """Raised when adding a unit symbol the project (or the preset list) already has."""


class ChiffragePermissionDeniedError(Exception):
    """Raised when the acting user may read the project but not modify its chiffrage."""


class NotProjectMemberError(Exception):
    """Raised when the acting user is not a member of the project."""
