"""SQLAlchemy adapter: copy a company's workers onto its projects that lack them."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.application.labor.enroll_company_workers import ICompanyWorkerRoster

# The copy source for a person is their most recently created active worker row, or, when
# they have none, their company directory profile with a default daily rate
# (name, phone, rate, role, linked account). A role from another company and an
# account already linked to a different worker of the target project are not copied.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(:key))")

_SET_ACTIVE = text(
    """
    UPDATE workers
    SET is_active = :active, updated_at = (now() AT TIME ZONE 'utc')
    WHERE person_id = :person_id
      AND is_active <> :active
      AND project_id IN (SELECT id FROM projects WHERE company_id = :company_id)
    """
)

_ENROLL = text(
    """
    WITH candidates AS (
        SELECT w.person_id, w.name, w.phone, w.daily_rate, w.role_id, w.user_id,
               0 AS source_rank, w.created_at AS source_at
        FROM workers w
        JOIN projects sp ON sp.id = w.project_id
        WHERE sp.company_id = :company_id
          AND w.is_active
          AND w.person_id IS NOT NULL
        UNION ALL
        SELECT cp.person_id, pe.name, pe.phone, cp.default_daily_rate, cp.labor_role_id, pe.user_id,
               1, cp.created_at
        FROM company_persons cp
        JOIN persons pe ON pe.id = cp.person_id
        WHERE cp.company_id = :company_id
          AND cp.is_active
          AND cp.pending_expires_at IS NULL
          AND cp.default_daily_rate > 0
    ),
    src AS (
        SELECT DISTINCT ON (person_id) person_id, name, phone, daily_rate, role_id, user_id
        FROM candidates
        WHERE (CAST(:person_id AS uuid) IS NULL OR person_id = CAST(:person_id AS uuid))
        ORDER BY person_id, source_rank, source_at DESC
    )
    INSERT INTO workers
        (id, project_id, person_id, name, phone, daily_rate, role_id, user_id, is_active, created_at, updated_at)
    SELECT gen_random_uuid(), p.id, s.person_id, s.name, s.phone, s.daily_rate,
           CASE WHEN EXISTS (SELECT 1 FROM labor_roles r
                             WHERE r.id = s.role_id AND r.company_id = :company_id)
                THEN s.role_id END,
           CASE WHEN s.user_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM workers u
                                 WHERE u.project_id = p.id AND u.user_id = s.user_id)
                THEN s.user_id END,
           TRUE, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc')
    FROM projects p
    CROSS JOIN src s
    WHERE p.company_id = :company_id
      AND (CAST(:project_id AS uuid) IS NULL OR p.id = CAST(:project_id AS uuid))
      AND NOT EXISTS (SELECT 1 FROM workers x
                      WHERE x.project_id = p.id AND x.person_id = s.person_id)
    """
)


class SqlAlchemyCompanyWorkerRoster(ICompanyWorkerRoster):
    def __init__(self, session: Session):
        self._session = session

    def _company_of(self, project_id: UUID) -> Optional[UUID]:
        return self._session.execute(text("SELECT company_id FROM projects WHERE id = :p"), {"p": project_id}).scalar()

    def _enroll(self, company_id: UUID, project_id: Optional[UUID], person_id: Optional[UUID]) -> int:
        try:
            # One writer per company at a time: two concurrent copies must not both add the same person.
            self._session.execute(_LOCK, {"key": f"company-workers:{company_id}"})
            result = self._session.execute(
                _ENROLL, {"company_id": company_id, "project_id": project_id, "person_id": person_id}
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return result.rowcount or 0

    def enroll_person(self, project_id: UUID, person_id: UUID) -> int:
        company_id = self._company_of(project_id)
        return 0 if company_id is None else self._enroll(company_id, None, person_id)

    def enroll_company_workers_in_project(self, project_id: UUID) -> int:
        company_id = self._company_of(project_id)
        return 0 if company_id is None else self._enroll(company_id, project_id, None)

    def set_person_active(self, project_id: UUID, person_id: UUID, active: bool) -> int:
        company_id = self._company_of(project_id)
        if company_id is None:
            return 0
        try:
            self._session.execute(_LOCK, {"key": f"company-workers:{company_id}"})
            result = self._session.execute(
                _SET_ACTIVE, {"active": active, "person_id": person_id, "company_id": company_id}
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return result.rowcount or 0

    def sync_company(self, company_id: UUID) -> int:
        return self._enroll(company_id, None, None)
