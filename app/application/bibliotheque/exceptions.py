"""Application-layer exceptions for the bibliotheque use-cases."""


class BibliothequeError(Exception):
    """Base exception for all bibliotheque application errors."""

    pass


class ProductNotFoundError(BibliothequeError):
    """Raised when a requested product does not exist or is not accessible."""

    pass


class CompanyAccessDeniedError(BibliothequeError):
    """Raised when the requester has no membership in the target company."""

    pass


class InsufficientPermissionError(BibliothequeError):
    """Raised when the requester lacks the required named permission (e.g. bibliotheque:manage)."""

    pass


class ImageTooLargeError(BibliothequeError):
    """Raised when the uploaded image exceeds the allowed byte limit."""

    pass


class UnsupportedImageTypeError(BibliothequeError):
    """Raised when the uploaded image content-type is not in the allowed set."""

    pass


class SsrfBlockedError(BibliothequeError):
    """Raised when the requested image URL is not in the SSRF allowlist or uses a disallowed scheme."""

    pass


class ImageAlreadyExistsError(BibliothequeError):
    """Raised when the product already has an image and force=True was not requested."""

    pass
