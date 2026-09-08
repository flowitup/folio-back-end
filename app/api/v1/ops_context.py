"""Platform-ops lookup for API routes — the single replacement for the `*:*` JWT claim.

Platform ops (`users.is_platform_ops`) is the flowitup support bypass: it is
NOT a role and never comes from the token, so revoking it applies on the very
next request instead of at the next login. Every route that used to read
`"*:*" in get_jwt()["permissions"]` calls `is_platform_ops()` here.

The value is read through `AuthzReaderPort` (same per-request cache as the rest
of the resolver, see `app.api.v1.authz_context.get_reader_cache`), so repeated
calls within one request cost one query. Degrades to `False` — never raises —
outside a request, without a JWT identity, or when no reader is wired (minimal
test fixtures).
"""

from __future__ import annotations

from uuid import UUID

from flask_jwt_extended import get_jwt_identity


def _coerce(user_id: "UUID | str | None") -> "UUID | None":
    if user_id is None:
        return None
    if isinstance(user_id, UUID):
        return user_id
    try:
        return UUID(str(user_id))
    except (ValueError, TypeError):
        return None


def is_platform_ops(user_id: "UUID | str | None" = None) -> bool:
    """Return True when the user holds the platform-ops flag.

    Args:
        user_id: the user to check; defaults to the current JWT identity.
    """
    if user_id is None:
        try:
            user_id = get_jwt_identity()
        except RuntimeError:  # no request/JWT context
            return False
    uid = _coerce(user_id)
    if uid is None:
        return False

    from wiring import get_container

    reader = getattr(get_container(), "authz_reader", None)
    if reader is None:
        return False
    return bool(reader.is_platform_ops(uid))
