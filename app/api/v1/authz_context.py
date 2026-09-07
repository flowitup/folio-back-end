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
"""

from __future__ import annotations

from uuid import UUID

import flask

from app.domain.authz.resolver import effective_permissions

_MEMO_ATTR = "_authz_resolver_memo"


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
