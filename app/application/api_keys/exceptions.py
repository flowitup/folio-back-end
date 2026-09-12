"""Application exceptions for the api_keys module."""

from __future__ import annotations


class ApiKeyNotFoundError(Exception):
    """Raised when a requested API key does not exist, or is not owned by the caller."""

    pass


class ApiKeyLimitReachedError(Exception):
    """Raised when a user tries to create more API keys than the per-user cap allows."""

    pass


class InvalidApiKeyNameError(ValueError):
    """Raised when an API key name is empty (after strip) or exceeds the length limit."""

    pass
