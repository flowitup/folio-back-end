"""SQLAlchemy adapter for IPendingAttendanceQuery — pending days a user may validate."""

from typing import List
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.application.labor.ports import IPendingAttendanceQuery, PendingAttendanceItem
from app.infrastructure.database.models import (
    LaborEntryModel,
    PermissionModel,
    PersonModel,
    ProjectModel,
    WorkerModel,
)
from app.infrastructure.database.models.associations import role_permissions, user_projects, user_roles

# Permission names that let a role validate attendance on a project.
_VALIDATOR_PERMISSIONS = ("project:manage_labor", "project:*", "*:*")


class SQLAlchemyPendingAttendanceQuery(IPendingAttendanceQuery):
    """Single query: pending entries → worker → project, filtered to projects the
    user can validate (owner, or member whose membership role or global role
    carries manage_labor). Written in Core so it runs on SQLite in tests too."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_pending_for_validator(self, user_id: UUID, limit: int = 100) -> List[PendingAttendanceItem]:
        # Membership role on this project grants manage_labor.
        membership_grants = exists(
            select(1)
            .select_from(
                user_projects.join(role_permissions, role_permissions.c.role_id == user_projects.c.role_id).join(
                    PermissionModel, PermissionModel.id == role_permissions.c.permission_id
                )
            )
            .where(
                user_projects.c.user_id == user_id,
                user_projects.c.project_id == ProjectModel.id,
                PermissionModel.name.in_(_VALIDATOR_PERMISSIONS),
            )
        )
        # Member of the project (any role) AND a global role grants manage_labor.
        is_member = exists(
            select(1).where(user_projects.c.user_id == user_id, user_projects.c.project_id == ProjectModel.id)
        )
        global_grants = exists(
            select(1)
            .select_from(
                user_roles.join(role_permissions, role_permissions.c.role_id == user_roles.c.role_id).join(
                    PermissionModel, PermissionModel.id == role_permissions.c.permission_id
                )
            )
            .where(user_roles.c.user_id == user_id, PermissionModel.name.in_(_VALIDATOR_PERMISSIONS))
        )

        worker_name = func.coalesce(PersonModel.name, WorkerModel.name)
        rows = (
            self._session.query(
                LaborEntryModel.id.label("entry_id"),
                ProjectModel.id.label("project_id"),
                ProjectModel.name.label("project_name"),
                WorkerModel.id.label("worker_id"),
                worker_name.label("worker_name"),
                LaborEntryModel.date.label("date"),
                LaborEntryModel.shift_type.label("shift_type"),
                LaborEntryModel.supplement_hours.label("supplement_hours"),
                LaborEntryModel.note.label("note"),
                LaborEntryModel.created_at.label("submitted_at"),
                LaborEntryModel.status.label("status"),
                LaborEntryModel.proposed_shift_type.label("proposed_shift_type"),
                LaborEntryModel.proposed_supplement_hours.label("proposed_supplement_hours"),
                LaborEntryModel.proposed_note.label("proposed_note"),
                LaborEntryModel.change_requested_at.label("change_requested_at"),
            )
            .join(WorkerModel, WorkerModel.id == LaborEntryModel.worker_id)
            .join(ProjectModel, ProjectModel.id == WorkerModel.project_id)
            .outerjoin(PersonModel, PersonModel.id == WorkerModel.person_id)
            .filter(
                or_(LaborEntryModel.status == "pending", LaborEntryModel.change_requested_at.isnot(None)),
                or_(ProjectModel.owner_id == user_id, membership_grants, is_member & global_grants),
            )
            .order_by(LaborEntryModel.date.desc(), LaborEntryModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            PendingAttendanceItem(
                entry_id=r.entry_id,
                project_id=r.project_id,
                project_name=r.project_name,
                worker_id=r.worker_id,
                worker_name=r.worker_name,
                date=r.date,
                shift_type=r.shift_type,
                supplement_hours=r.supplement_hours or 0,
                note=r.note,
                # A validated row with an open proposal is a change request; its "submitted"
                # moment is when the worker asked, not when the day was first logged.
                submitted_at=(
                    r.change_requested_at if r.status != "pending" and r.change_requested_at else r.submitted_at
                ),
                kind="attendance_pending" if r.status == "pending" else "attendance_change",
                proposed_shift_type=r.proposed_shift_type,
                proposed_supplement_hours=r.proposed_supplement_hours,
                proposed_note=r.proposed_note,
            )
            for r in rows
        ]
