"""Argon2 password hasher adapter.

No user password is hashed with this any more (phone + SMS code is the only
sign-in), but the class stays: it backs Argon2Hasher
(app/infrastructure/security/argon2_hasher.py), which hashes company invite
tokens — an unrelated feature with the same hash/verify shape.
"""

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError


class Argon2PasswordHasher:
    """Argon2 hash/verify — generic, not password-specific despite the name."""

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
