"""Invite token value object — generation and verification helpers."""

import hashlib
import hmac
import secrets


def generate_token() -> tuple[str, str]:
    """
    Generate a secure random invite token.

    Returns:
        (raw_token, sha256_hex) — caller emails raw_token, stores only sha256_hex.
    """
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_token(raw: str) -> str:
    """Return the sha256 hex digest of a raw token string (for verification)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def compare_token_hashes(a: str, b: str) -> bool:
    """Constant-time comparison of two token hashes to prevent timing attacks."""
    return hmac.compare_digest(a, b)
