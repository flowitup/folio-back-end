"""One definition of "may this user manage invitations on this project?".

Create, list and revoke all answer it the same way: the resolver's
`project:invite` on the invitation's own project (company admin anywhere in
their company, assigned manager on theirs, member only through an explicit D8
grant), plus the platform-ops support bypass. No legacy global role, no owner
bypass (D6) — a global `manager` row must not reach another company's
invitations.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.authz.resolver import has_permission

INVITE_PERMISSION = "project:invite"


def can_manage_project_invites(authz_reader: Any, user_id: UUID, project_id: UUID) -> bool:
    """Return True when `user_id` holds `project:invite` on `project_id`.

    Fails closed when no reader is wired: the routes calling these use-cases
    already resolve the same permission, so a wiring gap must deny rather than
    fall back to anything weaker.
    """
    if authz_reader is None:
        return False
    return has_permission(
        authz_reader,
        user_id,
        INVITE_PERMISSION,
        project_id=project_id,
        is_platform_admin=bool(authz_reader.is_platform_ops(user_id)),
    )
