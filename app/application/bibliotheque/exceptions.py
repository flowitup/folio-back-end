"""Application-layer exceptions for the bibliotheque use-cases."""


class BibliothequeError(Exception):
    """Base exception for all bibliotheque application errors."""

    pass


class ProductNotFoundError(BibliothequeError):
    """Raised when a requested product does not exist or is not accessible."""

    pass


class SupplierNotFoundError(BibliothequeError):
    """Raised when a referenced supplier does not exist."""

    pass


class CompanyAccessDeniedError(BibliothequeError):
    """Raised when the requester has no membership in the target company."""

    pass


class InsufficientPermissionError(BibliothequeError):
    """Raised when the requester lacks the required named permission (e.g. bibliotheque:manage)."""

    pass


class InvalidImportError(BibliothequeError):
    """Raised when the import payload fails validation (bad references, negative qty, etc.)."""

    pass
