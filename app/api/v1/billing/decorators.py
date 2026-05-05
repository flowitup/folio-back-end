"""RBAC/ownership decorators for billing routes.

require_billing_document_owner:
  - Loads billing document by <doc_id> URL param via the repo directly.
  - If not found → 404 (no existence leak).
  - If found but caller is not owner (and lacks *:* superadmin) → 404
    (mirrors invoice route pattern — avoids existence leak via 403).
  - On success: injects `billing_doc` keyword arg into the wrapped handler.

require_billing_template_owner:
  - Same pattern for <template_id> → injects `billing_template`.
"""

from __future__ import annotations

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity

from app.api.v1.projects.schemas import ErrorResponse


def _not_found(message: str):
    return (
        jsonify(ErrorResponse(error="NotFound", message=message, status_code=404).model_dump()),
        404,
    )


def _has_superadmin() -> bool:
    """Return True if the JWT carries the *:* wildcard permission."""
    jwt_claims = get_jwt()
    return "*:*" in jwt_claims.get("permissions", [])


def require_billing_document_owner(fn):
    """Decorator: load billing doc by <doc_id> → verify ownership → inject billing_doc.

    Calls the repo directly (not the use-case) so we can return 404 for both
    missing and unauthorised cases without a 403 existence leak.

    Expected decorator order on a route:
        @jwt_required()
        @limiter.limit(...)      # optional
        @require_billing_document_owner
        def my_route(doc_id: str, billing_doc, ...): ...
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from wiring import get_container

        doc_id_str = kwargs.get("doc_id")
        if not doc_id_str:
            return _not_found("Missing doc_id")
        try:
            doc_uuid = UUID(doc_id_str)
        except ValueError:
            return _not_found(f"Invalid doc id: {doc_id_str}")

        container = get_container()
        # Use the repo directly so we control the 404-for-both-cases response.
        doc = container.billing_document_repo.find_by_id(doc_uuid)
        if doc is None:
            return _not_found(f"Billing document {doc_id_str} not found")

        caller_id = UUID(get_jwt_identity())
        if doc.user_id != caller_id and not _has_superadmin():
            # Return 404, not 403 — avoids leaking that the document exists
            return _not_found(f"Billing document {doc_id_str} not found")

        kwargs["billing_doc"] = doc
        return fn(*args, **kwargs)

    return wrapper


def require_billing_template_owner(fn):
    """Decorator: load billing template by <template_id> → verify ownership.

    Injects `billing_template` into the handler kwargs.
    Returns 404 for both missing and unauthorised access (same leak-prevention pattern).
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from wiring import get_container

        tpl_id_str = kwargs.get("template_id")
        if not tpl_id_str:
            return _not_found("Missing template_id")
        try:
            tpl_uuid = UUID(tpl_id_str)
        except ValueError:
            return _not_found(f"Invalid template id: {tpl_id_str}")

        container = get_container()
        tpl = container.billing_template_repo.find_by_id(tpl_uuid)
        if tpl is None:
            return _not_found(f"Billing template {tpl_id_str} not found")

        caller_id = UUID(get_jwt_identity())
        if tpl.user_id != caller_id and not _has_superadmin():
            return _not_found(f"Billing template {tpl_id_str} not found")

        kwargs["billing_template"] = tpl
        return fn(*args, **kwargs)

    return wrapper
