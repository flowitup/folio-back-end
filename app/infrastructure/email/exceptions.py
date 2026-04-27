"""Email infrastructure exceptions."""


class EmailDeliveryError(Exception):
    """Raised when an email cannot be delivered via the configured provider."""
