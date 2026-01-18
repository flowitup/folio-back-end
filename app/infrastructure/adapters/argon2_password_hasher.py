"""Argon2 password hasher adapter."""

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError


class Argon2PasswordHasher:
    """Argon2 implementation of PasswordHasherPort."""

    def __init__(
        self,
        time_cost: int = 2,
        memory_cost: int = 65536,  # 64 MB
        parallelism: int = 1,
    ):
        self._hasher = Argon2Hasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
        )

    def hash(self, password: str) -> str:
        """Hash a plaintext password using Argon2."""
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        """Verify password against Argon2 hash."""
        try:
            self._hasher.verify(password_hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
