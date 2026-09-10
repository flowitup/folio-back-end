"""Budget scope — whether a caller may see a project's financing side.

Folio splits a project's money in two. The *spend* side (materials & services,
labor payments, the spent rollups) belongs to whoever runs the site, so an
assigned manager reads all of it through ``project:manage_labor`` /
``project:view_pay``. The *financing* side — the budget, what is left of it, and
the funds released to the company — is the owner's business and rides on its own
``project:view_budget`` permission, which the matrix grants to company admins
only. An admin who wants a particular manager to see it adds a D8 grant row
(``project:view_budget`` is in ``CUSTOMISABLE_PERMISSIONS``).

Read endpoints narrow instead of refusing: a caller without the permission gets
the invoice list without its ``released_funds`` rows and with the released-funds
aggregates zeroed, exactly like a restricted member in
:mod:`app.api.v1.projects.labor_scope`. Write endpoints do refuse — recording or
deleting a release you cannot see would be a blind edit — via
:func:`budget_forbidden`.
"""

from __future__ import annotations

from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from app.api.v1.projects.decorators import _effective_perms_for, _has_permission


def caller_sees_budget(project_id: UUID | str) -> bool:
    """Resolver ``project:view_budget`` on this project (wildcards honoured).

    No owner bypass (D6): the creator of a project holds this through their
    company role and D8 rows like everyone else.
    """
    project_uuid = UUID(str(project_id))
    user_id = UUID(str(get_jwt_identity()))
    return _has_permission(_effective_perms_for(project_uuid, user_id), "project:view_budget")


def budget_forbidden():
    """Uniform 403 body for writes to the financing side of a project."""
    return (
        jsonify(
            {
                "error": "Forbidden",
                "message": "Released funds are only visible to the company admin",
                "status_code": 403,
            }
        ),
        403,
    )
