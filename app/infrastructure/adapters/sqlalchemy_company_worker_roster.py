"""SQLAlchemy adapter: copy a company's workers onto its projects that lack them."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.application.labor.enroll_company_workers import ICompanyWorkerRoster

# The copy source for a person is their most recently created active worker row
# (name, phone, rate, role, linked account). A role from another company and an
# account already linked to a different worker of the target project are not copied.
_ENROLL = text(
    """
    WITH src AS (
        SELECT DISTINCT ON (w.person_id)
               w.person_id, w.name, w.phone, w.daily_rate, w.role_id, w.user_id
        FROM workers w
        JOIN projects sp ON sp.id = w.project_id
        WHERE sp.company_id = :company_id
          AND w.is_active
          AND w.person_id IS NOT NULL
          AND (CAST(:person_id AS uuid) IS NULL OR w.person_id = CAST(:person_id AS uuid))
        ORDER BY w.person_id, w.created_at DESC
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
        return self._session.execute(
            text("SELECT company_id FROM projects WHERE id = :p"), {"p": project_id}
        ).scalar()

    def _enroll(self, company_id: UUID, project_id: Optional[UUID], person_id: Optional[UUID]) -> int:
        try:
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

    def sync_company(self, company_id: UUID) -> int:
        return self._enroll(company_id, None, None)
