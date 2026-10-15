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

import functools
import logging
import re
from datetime import timezone
from typing import Any, Callable

from flask import g, jsonify, request

from app.application.api_keys.token_factory import API_KEY_PREFIX
from app.domain.value_objects.invite_token import hash_token

logger = logging.getLogger(__name__)

# One line per key-authenticated write; its own logger so the line is emitted at INFO whatever the
# root level is (gunicorn leaves it at WARNING), and so it can be filtered on its own.
audit_logger = logging.getLogger("folio.api_key_audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    audit_logger.addHandler(_audit_handler)


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
    g.api_key_id = None
    g.api_key_name = None
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

        # A key reaches every tenant its owner can: for a platform-ops account that is every company,
        # and the key never expires, so a key owned by one is never honoured (nor created).
        if _owner_is_platform_ops(container, row):
            logger.warning("API key refused: owned by a platform-ops account key_id=%s user_id=%s", row.id, row.user_id)
            return

        # A key made before the owner's sessions cut-off (a verified phone change signs every
        # other device out) is as dead as a JWT of that time: the token minted below always
        # carries a fresh iat, so the cut-off has to be applied to the key itself.
        if not _key_may_sign_in(container, row):
            return

        minted = token_issuer.create_access_token(row.user_id)
        # EnvironHeaders reads straight through to environ, so this is visible
        # to flask-jwt-extended's own header lookup later in this request.
        request.environ["HTTP_AUTHORIZATION"] = f"Bearer {minted}"
        g.authenticated_via_api_key = True
        g.api_key_id = row.id
        g.api_key_name = row.name
        _log_key_write(row)

        repo.touch_last_used(row.id)
    except Exception:
        logger.exception("API key authentication failed unexpectedly; request stays unauthenticated.")


def _owner_is_platform_ops(container: Any, row: Any) -> bool:
    """True when the key's owner holds the platform-ops flag (False when no reader is wired)."""
    reader = getattr(container, "authz_reader", None)
    return reader is not None and bool(reader.is_platform_ops(row.user_id))


_LOG_UNSAFE = re.compile(r"[^\x20-\x7e]")


def _log_safe(value: Any, limit: int = 200) -> str:
    """Printable ASCII only and bounded, so a header or path cannot forge or flood a log line."""
    return _LOG_UNSAFE.sub("?", str(value or ""))[:limit]


def _log_key_write(row: Any) -> None:
    """One structured line per key-authenticated request that is not a read: who, what, from where.

    Never the credential, headers (beyond the user agent) or the body.
    """
    if request.method in _SAFE_METHODS:
        return
    rule = request.url_rule.rule if request.url_rule is not None else request.path
    audit_logger.info(
        "api_key_use key_id=%s user_id=%s method=%s path=%s remote_addr=%s user_agent=%s",
        row.id,
        row.user_id,
        request.method,
        _log_safe(rule),
        _log_safe(request.remote_addr, 64),
        _log_safe(request.headers.get("User-Agent")),
    )


def _key_may_sign_in(container: Any, row: Any) -> bool:
    """True when the key's owner may sign in and the key was not made before their cut-off."""
    check = getattr(getattr(container, "user_repository", None), "is_sign_in_allowed", None)
    if check is None:
        return True
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return bool(check(row.user_id, issued_at=int(created_at.timestamp())))


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


def deny_api_key_callers(view: Callable[..., Any]) -> Callable[..., Any]:
    """Route decorator: refuse a key-authenticated caller on this one route.

    For a write on an otherwise readable blueprint whose sibling writes a key legitimately needs
    (the notifications blueprint: a key lists and dismisses reminders, but must not mute categories
    or clear the activity feed to hide a compromise).
    """

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if g.get("authenticated_via_api_key"):
            return _forbidden("API keys cannot change notification settings")
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Route policy — which writes a key may make (default-deny)
# ---------------------------------------------------------------------------
#
# A key may do the day-to-day work its owner does (the MCP plugin: projects, invoices, labor,
# documents, photos, notes, tasks, payment methods, ...) but must not be able to turn a leak into a
# durable seat: adding or promoting company members, join codes, grants, invitations, project
# assignments, deleting a company, or anything that mints access for another account. Those need an
# interactive session. Every WRITE route is therefore refused for a key unless its blueprint or its
# endpoint is listed here: a new route is closed to keys until someone adds it on purpose (the
# route-matrix test in tests/api/test_api_key_authentication.py fails on any change to this set).
# Reads are never affected.

KEY_WRITE_BLUEPRINTS = frozenset(
    {
        "bibliotheque",
        "billing_documents",
        "billing_templates",
        "chiffrage",
        "inventory",
        "invoices",
        "labor",
        "notes",
        "notifications",  # settings writes carry deny_api_key_callers
        "payment_methods",
        "persons",
        "project_analyses",
        "project_documents",
        "project_photos",
        "tasks",
    }
)

# Blueprints that also hold membership/identity routes: only these endpoints are open to a key.
KEY_WRITE_ENDPOINTS = frozenset(
    {
        "companies.create_company",
        "companies.update_company",
        "projects.create_project",
        "projects.update_project",
        "projects.delete_project",
        "users_me.set_primary_company",
    }
)


def enforce_api_key_route_policy():
    """App `before_request` (after the key seam): refuse a key on any write route not listed above."""
    if request.method in _SAFE_METHODS or not g.get("authenticated_via_api_key"):
        return None
    endpoint = request.endpoint
    if endpoint is None:  # no such route: the router answers 404/405
        return None
    if request.blueprint in KEY_WRITE_BLUEPRINTS or endpoint in KEY_WRITE_ENDPOINTS:
        return None
    return _forbidden("API keys cannot manage members, access or invitations")
