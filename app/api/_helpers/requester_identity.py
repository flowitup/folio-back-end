"""Helper to resolve the acting user's email for audit/metadata fields.

The JWT issued by this application carries only ``permissions`` as additional
claims (see app/application/usecases/login.py) — no ``email`` claim. We must
therefore always fall back to a user-repository lookup. This helper centralises
that logic so every route shares a single implementation.
"""

from __future__ import annotations

from uuid import UUID

from flask_jwt_extended import get_jwt_identity


def get_requester_email(user_repository) -> str:  # type: ignore[no-untyped-def]
    """Return the authenticated user's email address for export metadata.

    Looks up the user by JWT identity (UUID string). Returns ``"unknown"`` if
    the identity is missing or the user row cannot be found — this should not
    happen in practice under normal auth flow.

    Args:
        user_repository: Any repository implementing ``find_by_id(UUID)``.

    Returns:
        Email string, or ``"unknown"`` as a safe fallback.
    """
    try:
        raw_id = get_jwt_identity()
        if not raw_id:
            return "unknown"
        user = user_repository.find_by_id(UUID(raw_id))
        return user.email if user else "unknown"
    except Exception:
        return "unknown"
