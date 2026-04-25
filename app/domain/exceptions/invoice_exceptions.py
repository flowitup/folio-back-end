"""Invoice domain exceptions."""


class InvoiceNotFoundError(Exception):
    """Raised when an invoice cannot be found by ID."""
    pass


class InvalidInvoiceDataError(ValueError):
    """Raised when invoice data fails validation."""
    pass


class InvoiceNumberConflictError(Exception):
    """Raised when invoice number conflicts within a project."""
    pass
