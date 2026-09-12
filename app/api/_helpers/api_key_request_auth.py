"""API-key authentication seam — the entire job is establishing identity.

Since the D6 authz redesign (see app/api/v1/projects/decorators.py), no
route reads a token's `permissions` claim: every authorization decision
resolves company role + project assignment + D8 grants from the database,
keyed on `get_jwt_identity()`, and `app/api/v1/ops_context.py` reads
`users.is_platform_ops` the same way. So an API key does not need its own
authorization path at all — it only needs to become an identity that
flask-jwt-extended itself recognizes.

That is what this module does, and why: it recognizes a `folio_sk_...`
credential exactly once, up front, then mints a normal short-lived access
token for the key's owner through the SAME `TokenIssuerPort.create_access_token`
every login path already uses, and substitutes it into the request's
Authorization header. flask-jwt-extended then verifies THAT token through its
own normal verification path, so `@jwt_required`, `get_jwt_identity`,
`get_jwt`, the authz resolver, `is_platform_ops`, and the Flask-Limiter
`jwt_user_key` all behave identically for a browser session and an API key —
no route, decorator, or resolver needs to change, or even know API keys
exist.

Because Werkzeug's `EnvironHeaders` is a live, read-through view over
`request.environ`, mutating `request.environ["HTTP_AUTHORIZATION"]` here is
visible to every subsequent header read in this request, including
flask-jwt-extended's own.

`g.authenticated_via_api_key` is explicitly reset to False on every call,
the same way `_clear_authz_request_memo` resets its own per-request memo:
Flask reuses an already-pushed AppContext for a request whose app matches
the top of the stack, so anything that holds one `app.app_context()` open
across several requests (a test fixture wrapping many `test_client()` calls
in one `with app.app_context():` block, for example) would otherwise leak
this flag from one request into the next.
"""

from __future__ import annotations

import logging

from flask import g, jsonify, request

from app.application.api_keys.token_factory import API_KEY_PREFIX
from app.domain.value_objects.invite_token import hash_token

logger = logging.getLogger(__name__)


def _extract_candidate_credential() -> str | None:
    """Return the raw credential from `Authorization: Bearer <v>`, else `X-API-Key`."""
    auth_header = request.headers.get("Authorization", "")
    # RFC 7235 makes the auth-scheme token case-insensitive, and real HTTP
    # clients do send "bearer". Matching case-sensitively would silently drop
    # the credential and answer 401 for a perfectly valid key.
    scheme, _, rest = auth_header.partition(" ")
    if scheme.lower() == "bearer":
        candidate = rest.strip()
        if candidate:
            return candidate

    api_key_header = request.headers.get("X-API-Key", "").strip()
    return api_key_header or None


def authenticate_api_key_request() -> None:
    """Recognize an API-key credential on the current request and swap in a minted JWT.

    Registered as a `before_request` hook in `create_app`, next to
    `_clear_authz_request_memo`. Every branch that is not an unambiguous
    match returns immediately without mutating anything, so the ordinary JWT
    auth path — including its usual 401 — is completely unaffected:

      * no credential at all, on either header → return.
      * credential does not start with `API_KEY_PREFIX` → an ordinary JWT
        takes exactly the path it takes today.
      * credential hashes to no known key → return without touching
        anything, so the normal `@jwt_required()` 401 answers; there is no
        separate "invalid API key" error shape.

    Any unexpected failure is logged and swallowed rather than raised: an
    outage in this seam must degrade to "request stays unauthenticated",
    never to a 500.
    """
    # Reset unconditionally, before the try — see module docstring for why a
    # stale True from a previous request must never survive into this one.
    g.authenticated_via_api_key = False
    try:
        credential = _extract_candidate_credential()
        if credential is None or not credential.startswith(API_KEY_PREFIX):
            return

        from wiring import get_container

        container = get_container()
        repo = getattr(container, "api_key_repository", None)
        token_issuer = getattr(container, "token_issuer", None)
        if repo is None or token_issuer is None:
            return

        row = repo.find_by_token_hash(hash_token(credential))
        if row is None:
            return

        minted = token_issuer.create_access_token(row.user_id)
        # EnvironHeaders reads straight through to environ, so this is visible
        # to flask-jwt-extended's own header lookup later in this request.
        request.environ["HTTP_AUTHORIZATION"] = f"Bearer {minted}"
        g.authenticated_via_api_key = True

        repo.touch_last_used(row.id)
    except Exception:
        logger.exception("API key authentication failed unexpectedly; request stays unauthenticated.")


# ---------------------------------------------------------------------------
# Blueprint guards — what an API key may NOT reach
# ---------------------------------------------------------------------------
#
# A key inherits its owner's permissions in full (locked decision D1), which
# governs what it may DO with those permissions. It deliberately does NOT
# extend to changing the account's authentication factor or administering
# accounts: `users.phone` IS the sign-in identity (phone + SMS code is the only
# way in), so a caller able to rewrite it converts a leaked, never-expiring key
# into permanent account takeover — rewrite the phone, receive the SMS, and the
# resulting session is an ordinary interactive one that is NOT
# `authenticated_via_api_key`, so it walks straight past the key-management
# guard below and locks the real owner out for good.
#
# Reads stay open: an automation legitimately calls `GET /auth/me` to discover
# which account its key belongs to.

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _forbidden(message: str):
    return (
        jsonify({"error": "Forbidden", "message": message, "status_code": 403}),
        403,
    )


def reject_api_key_callers():
    """Blueprint `before_request`: refuse EVERY request that authenticated with a key.

    Used by the API-key management blueprint itself. A leaked key must not be
    able to enumerate its siblings, mint itself replacements indefinitely, or
    revoke another key to cover its tracks; escalating past the key requires an
    ordinary interactive session.
    """
    if g.get("authenticated_via_api_key"):
        return _forbidden("API keys cannot manage API keys")
    return None


def reject_api_key_mutations():
    """Blueprint `before_request`: a key may READ these routes but never mutate them.

    Registered on the auth and admin blueprints. Safe methods pass (so
    `GET /auth/me` keeps working, and CORS preflight is unaffected); anything
    that writes is refused for a key-authenticated caller.
    """
    if request.method in _SAFE_METHODS:
        return None
    if g.get("authenticated_via_api_key"):
        return _forbidden("API keys cannot modify account or administration settings")
    return None
