"""Argon2 hasher adapter for the companies security port.

Re-exports the existing Argon2PasswordHasher from app.infrastructure.adapters
under the name expected by the companies application layer (Argon2HasherPort).

The existing adapter already satisfies the Argon2HasherPort structural protocol
(both expose .hash(str) -> str and .verify(str, str) -> bool with identical
semantics). This module avoids duplicating the implementation while keeping the
companies layer's import path self-contained.
"""

from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher as _Base


class Argon2Hasher(_Base):
    """Thin subclass alias.

    Exposes Argon2PasswordHasher as ``Argon2Hasher`` under the infrastructure
    security package. Callers in the companies wiring can import:

        from app.infrastructure.security.argon2_hasher import Argon2Hasher

    and pass it where an Argon2HasherPort is expected.
    No behaviour change — all logic lives in the parent class.
    """


__all__ = ["Argon2Hasher"]
