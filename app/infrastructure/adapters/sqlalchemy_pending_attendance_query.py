"""SQLAlchemy adapter for IPendingAttendanceQuery — pending days a user may validate."""

from typing import List
from uuid import UUID

from sqlalchemy import func, literal, or_
from sqlalchemy.orm import Session

from app.application.labor.ports import IPendingAttendanceQuery, PendingAttendanceItem
from app.infrastructure.database.labor_validation_scope import may_validate_clause
from app.infrastructure.database.models import (
    LaborEntryModel,
    PersonModel,
    ProjectModel,
    UserModel,
    WorkerModel,
)


class SQLAlchemyPendingAttendanceQuery(IPendingAttendanceQuery):
    """Single query: pending entries → worker → project, filtered to the projects
    the user may validate (company admin, assigned manager, or a D8 grant of
    `project:manage_labor` — see `labor_validation_scope`). Platform ops sees
    every project, mirroring their support access elsewhere. Written in Core so
    it runs on SQLite in tests too."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_pending_for_validator(self, user_id: UUID, limit: int = 100) -> List[PendingAttendanceItem]:
        is_ops = bool(self._session.query(UserModel.is_platform_ops).filter(UserModel.id == user_id).scalar())
        may_validate = (
            literal(True)
            if is_ops
            else may_validate_clause(self._session, user_id, ProjectModel.id, ProjectModel.company_id)
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
                may_validate,
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
