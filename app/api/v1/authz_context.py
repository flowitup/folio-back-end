"""Per-request memoization for the company-aware authz resolver.

A single request can touch the resolver from several places (a route
decorator, then again while serializing `my_permissions` on the response
body). `resolve_for_request` memoizes on `flask.g`, which Flask already
scopes to one request/app-context — no explicit teardown is needed, a new
`g` is created per request.

Degrades gracefully — no memoization, but still correct — when called outside
an app context (unit tests that exercise `app.api.v1.projects.decorators`
directly with monkeypatched `get_container`, with no Flask app/request
pushed), and to an empty permission set when `container.authz_reader` is
unset (e.g. a test app fixture that wires only the pieces it needs), so call
sites never need a None-check or app-context guard of their own.

`get_reader_cache()` exposes a second, lower-level per-request dict that
`SqlAlchemyAuthzReader` consults directly (see its `cache_provider`
constructor argument) so repeated calls for the SAME sub-query — e.g.
`company_role_for(user_id, company_id)` once per project while listing N
projects of one company — hit the cache instead of re-querying the DB. This
is a finer grain than the `resolve_for_request` memo above, which only
collapses calls that share the exact same `(user_id, project_id, company_id,
is_platform_admin)` key.

`clear_request_memo()` is registered as a Flask `before_request` hook (see
`app/__init__.py::create_app`) so every one of these caches is dropped at the
start of each request — a role or D8 grant/deny change must apply on the very
next request. This matters even though `flask.g` is nominally request-scoped:
Flask reuses an already-pushed `AppContext` for a request whose app matches
the one on top of the stack, so a test fixture (or any code) that holds one
`app.app_context()` open across several `test_client()` calls would otherwise
see the SAME `flask.g` — and therefore the SAME stale memo — for every one of
those calls. `before_request` handlers still run once per real request
regardless of app-context reuse, so clearing there is sufficient.
"""

from __future__ import annotations

from uuid import UUID

import flask

from app.domain.authz.resolver import denied_permissions, effective_permissions

_MEMO_ATTR = "_authz_resolver_memo"
_DENY_MEMO_ATTR = "_authz_resolver_deny_memo"
_READER_CACHE_ATTR = "_authz_reader_cache"


def get_reader_cache() -> dict:
    """Return the per-request cache dict backing `SqlAlchemyAuthzReader`, creating it lazily.

    Outside an app context this returns a fresh, unshared dict every call —
    i.e. no caching, but still correct (used by unit tests that construct a
    reader directly without a Flask request pushed).
    """
    if not flask.has_app_context():
        return {}
    cache = getattr(flask.g, _READER_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(flask.g, _READER_CACHE_ATTR, cache)
    return cache


def clear_request_memo() -> None:
    """Drop every per-request authz cache. Call once at the start of each request."""
    if not flask.has_app_context():
        return
    for attr in (_MEMO_ATTR, _DENY_MEMO_ATTR, _READER_CACHE_ATTR):
        if hasattr(flask.g, attr):
            delattr(flask.g, attr)


def resolve_for_request(
    user_id: UUID,
    *,
    project_id: "UUID | None" = None,
    company_id: "UUID | None" = None,
    is_platform_admin: bool = False,
) -> "frozenset[str]":
    """Return the resolver's effective permissions for this request, memoized.

    Same arguments as `app.domain.authz.resolver.effective_permissions`; the
    `AuthzReaderPort` implementation is read from the DI container so callers
    (decorators, serializers) don't need to thread it through by hand.
    """
    key = (user_id, project_id, company_id, is_platform_admin)
    memo = None
    if flask.has_app_context():
        memo = getattr(flask.g, _MEMO_ATTR, None)
        if memo is None:
            memo = {}
            setattr(flask.g, _MEMO_ATTR, memo)
        if key in memo:
            return memo[key]

    from wiring import get_container

    reader = getattr(get_container(), "authz_reader", None)
    if reader is None:
        result: "frozenset[str]" = frozenset({"*:*"}) if is_platform_admin else frozenset()
    else:
        result = effective_permissions(
            reader,
            user_id,
            project_id=project_id,
            company_id=company_id,
            is_platform_admin=is_platform_admin,
        )
    if memo is not None:
        memo[key] = result
    return result


def resolve_denied_for_request(
    user_id: UUID,
    *,
    project_id: "UUID | None" = None,
    company_id: "UUID | None" = None,
    is_platform_admin: bool = False,
) -> "frozenset[str]":
    """Return the resolver's D8 deny set for this request, memoized.

    Companion to `resolve_for_request` — a caller that unions its own
    permission set with the resolver's ALLOWED output (e.g.
    `app.api.v1.projects.decorators._effective_permissions`) needs the DENY
    set too, so an admin-managed deny row can override a permission a legacy
    global role also happens to grant (H3: deny must win over the legacy
    union). Same memoization/degradation behavior as `resolve_for_request`.
    """
    key = (user_id, project_id, company_id, is_platform_admin)
    memo = None
    if flask.has_app_context():
        memo = getattr(flask.g, _DENY_MEMO_ATTR, None)
        if memo is None:
            memo = {}
            setattr(flask.g, _DENY_MEMO_ATTR, memo)
        if key in memo:
            return memo[key]

    from wiring import get_container

    reader = getattr(get_container(), "authz_reader", None)
    if reader is None:
        result: "frozenset[str]" = frozenset()
    else:
        result = denied_permissions(
            reader,
            user_id,
            project_id=project_id,
            company_id=company_id,
            is_platform_admin=is_platform_admin,
        )
    if memo is not None:
        memo[key] = result
    return result
