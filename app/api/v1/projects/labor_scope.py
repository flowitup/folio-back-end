"""Worker scope — what a project member may see of labor and pay data.

A caller who holds ``project:manage_labor`` on a project (global role, membership
role, or project owner) sees every worker. Everyone else is a *restricted member*:
they only see the worker linked to their own account (``workers.user_id``) — their
attendance, their pay, their summary — and nothing of the project's money.

Route handlers call :func:`labor_scope_for` and narrow their query / response;
endpoints that cannot be narrowed (whole-project exports, tag cost rollups…) use
:func:`require_full_project_view` and answer 403 to restricted members.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Optional
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from app.api.v1.projects.decorators import _effective_perms_for, _has_permission


@dataclass(frozen=True)
class LaborScope:
    """``restricted`` → only ``worker_id`` (None when the account is not linked) may be shown."""

    restricted: bool
    worker_id: Optional[UUID]

    def allows_worker(self, worker_id: UUID | str) -> bool:
        return not self.restricted or (self.worker_id is not None and str(self.worker_id) == str(worker_id))


def caller_manages_labor(project_id: UUID, user_id: UUID) -> bool:
    """Owner, or effective ``project:manage_labor`` (global ∪ membership role, wildcards honoured)."""
    from wiring import get_container

    project_repo = getattr(get_container(), "project_repository", None)
    project = project_repo.find_by_id(project_id) if project_repo is not None else None
    if project is not None and project.owner_id == user_id:
        return True
    return _has_permission(_effective_perms_for(project_id, user_id), "project:manage_labor")


def labor_scope_for(project_id: UUID | str) -> LaborScope:
    """Resolve the scope of the current JWT caller on ``project_id``."""
    from wiring import get_container

    project_uuid = UUID(str(project_id))
    user_id = UUID(str(get_jwt_identity()))
    if caller_manages_labor(project_uuid, user_id):
        return LaborScope(restricted=False, worker_id=None)
    worker_repo = getattr(get_container(), "worker_repository", None)
    worker = worker_repo.find_by_project_and_user(project_uuid, user_id) if worker_repo is not None else None
    return LaborScope(restricted=True, worker_id=worker.id if worker is not None else None)


def restricted_forbidden():
    """Uniform 403 body for endpoints closed to restricted members."""
    return (
        jsonify(
            {
                "error": "Forbidden",
                "message": "Only project managers may view this",
                "status_code": 403,
            }
        ),
        403,
    )


def require_full_project_view(fn):
    """Decorator: 403 for restricted members. Place below ``require_project_access``."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if labor_scope_for(kwargs["project_id"]).restricted:
            return restricted_forbidden()
        return fn(*args, **kwargs)

    return wrapper
