"""Project document domain exceptions."""


class ProjectDocumentNotFoundError(Exception):
    """Raised when a project document cannot be found by ID or has been soft-deleted."""

    pass
