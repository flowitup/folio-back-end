"""Cryptographically-secure opaque token generator adapter.

Implements SecureTokenGeneratorPort using the stdlib secrets module.
Tokens are base64url-encoded (URL-safe alphabet, no padding) random bytes
suitable for use as single-use invite tokens.
"""

import secrets


class SecureTokenGenerator:
    """Production adapter for SecureTokenGeneratorPort.

    Uses secrets.token_urlsafe(byte_length) which reads from the OS CSPRNG
    (/dev/urandom on POSIX, BCryptGenRandom on Windows). The output is
    base64url-encoded, giving approximately 1.33 * byte_length characters.

    At byte_length=32 this produces ~43-character tokens with 256 bits of
    entropy — infeasible to brute-force even without argon2 on the server side.
    """

    def generate(self, byte_length: int = 32) -> str:
        """Return a base64url-encoded string of *byte_length* random bytes.

        Args:
            byte_length: Number of random bytes to generate. Default 32 (256-bit).

        Returns:
            URL-safe base64-encoded string with no padding characters.
        """
        if byte_length < 16:  # noqa: PLR2004 — minimum sane entropy floor
            raise ValueError(f"byte_length must be at least 16 for acceptable entropy; got {byte_length}")
        return secrets.token_urlsafe(byte_length)


__all__ = ["SecureTokenGenerator"]
