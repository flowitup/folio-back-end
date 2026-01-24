"""Password hasher port - interface for password hashing."""

from typing import Protocol


class PasswordHasherPort(Protocol):
    """Port for password hashing operations."""

    def hash(self, password: str) -> str:
        """Hash a plaintext password."""
        ...

    def verify(self, password: str, password_hash: str) -> bool:
        """Verify password against hash. Returns True if match."""
        ...
