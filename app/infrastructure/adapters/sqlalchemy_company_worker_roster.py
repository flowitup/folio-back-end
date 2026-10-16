"""SQLAlchemy adapter: copy a company's workers onto its projects that lack them."""

from __future__ import annotations

from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Session

from app.application.labor.enroll_company_workers import ICompanyWorkerRoster

# The copy source for a person is their most recently edited active worker row (an edit,
# a stop/restart or a new rate change all count), or, when they have no worker row in the
# company at all, their company directory profile with a default daily rate (name, phone,
# rate, role, linked account). A person whose every worker row is stopped is not copied:
# the stop holds on sites created later. A copied row gets the source's base rate AND its
# rate-change timeline, so the same person costs the same per day on every site. A role
# from another company and an account already linked to a different worker of the target
# project are not copied.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(:key))")

# Restarting re-links the person's own account when the stop freed it (soft delete clears
# workers.user_id), the account is still in the company and the project slot is free.
_SET_ACTIVE = text(
    """
    UPDATE workers w
    SET is_active = :active,
        updated_at = (now() AT TIME ZONE 'utc'),
        user_id = CASE
            WHEN :active AND w.user_id IS NULL AND pe.user_id IS NOT NULL
                 AND EXISTS (SELECT 1 FROM user_company_access a
                             WHERE a.user_id = pe.user_id AND a.company_id = :company_id)
                 AND NOT EXISTS (SELECT 1 FROM workers u
                                 WHERE u.project_id = w.project_id AND u.user_id = pe.user_id)
            THEN pe.user_id ELSE w.user_id END
    FROM persons pe
    WHERE pe.id = w.person_id
      AND w.person_id = :person_id
      AND w.is_active <> :active
      AND w.project_id IN (SELECT id FROM projects WHERE company_id = :company_id)
      AND (CAST(:only_projects AS uuid[]) IS NULL OR w.project_id = ANY(CAST(:only_projects AS uuid[])))
    """
)

_PERSON_PROJECTS = text(
    """
    SELECT w.project_id FROM workers w JOIN projects p ON p.id = w.project_id
    WHERE p.company_id = :company_id AND w.person_id = :person_id
    """
)

_SET_ROLE_ON_WORKERS = text(
    """
    UPDATE workers
    SET role_id = :role_id, updated_at = (now() AT TIME ZONE 'utc')
    WHERE person_id = :person_id
      AND role_id IS DISTINCT FROM :role_id
      AND project_id IN (SELECT id FROM projects WHERE company_id = :company_id)
      AND (CAST(:only_projects AS uuid[]) IS NULL OR project_id = ANY(CAST(:only_projects AS uuid[])))
    """
)

_SET_ROLE_ON_PROFILE = text(
    """
    UPDATE company_persons SET labor_role_id = :role_id
    WHERE company_id = :company_id AND person_id = :person_id
      AND labor_role_id IS DISTINCT FROM :role_id
    """
)

_ENROLL = text(
    """
    WITH candidates AS (
        SELECT w.person_id, w.name, w.phone, w.daily_rate, w.role_id, w.user_id,
               w.id AS source_worker_id, 0 AS source_rank,
               GREATEST(COALESCE(w.updated_at, w.created_at, TIMESTAMP 'epoch'),
                        COALESCE((SELECT max(rc.created_at) FROM worker_rate_changes rc
                                  WHERE rc.worker_id = w.id), TIMESTAMP 'epoch')) AS source_at
        FROM workers w
        JOIN projects sp ON sp.id = w.project_id
        WHERE sp.company_id = :company_id
          AND w.is_active
          AND w.person_id IS NOT NULL
        UNION ALL
        SELECT cp.person_id, pe.name, pe.phone, cp.default_daily_rate, cp.labor_role_id, pe.user_id,
               CAST(NULL AS uuid), 1, CAST(cp.created_at AS timestamp)
        FROM company_persons cp
        JOIN persons pe ON pe.id = cp.person_id
        WHERE cp.company_id = :company_id
          AND cp.is_active
          AND cp.pending_expires_at IS NULL
          AND cp.default_daily_rate > 0
          AND NOT EXISTS (SELECT 1 FROM workers xw JOIN projects xp ON xp.id = xw.project_id
                          WHERE xp.company_id = :company_id AND xw.person_id = cp.person_id)
    ),
    src AS (
        SELECT DISTINCT ON (person_id) person_id, name, phone, daily_rate, role_id, user_id, source_worker_id
        FROM candidates
        WHERE (CAST(:person_id AS uuid) IS NULL OR person_id = CAST(:person_id AS uuid))
        ORDER BY person_id, source_rank, source_at DESC
    ),
    added AS (
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
        RETURNING id, person_id
    ),
    rates AS (
        INSERT INTO worker_rate_changes (id, worker_id, effective_date, daily_rate, created_at)
        SELECT gen_random_uuid(), a.id, rc.effective_date, rc.daily_rate, (now() AT TIME ZONE 'utc')
        FROM added a
        JOIN src s ON s.person_id = a.person_id
        JOIN worker_rate_changes rc ON rc.worker_id = s.source_worker_id
        RETURNING 1
    )
    SELECT count(*) FROM added
    """
)


def _uuid_list(project_ids: Optional[Sequence[UUID]]) -> Optional[list[str]]:
    return None if project_ids is None else [str(p) for p in project_ids]


class SqlAlchemyCompanyWorkerRoster(ICompanyWorkerRoster):
    def __init__(self, session: Session):
        self._session = session

    def _company_of(self, project_id: UUID) -> Optional[UUID]:
        return self._session.execute(text("SELECT company_id FROM projects WHERE id = :p"), {"p": project_id}).scalar()

    def _write(self, company_id: UUID, statements) -> int:
        """Run ``statements`` under the company lock in one transaction; returns the first one's count."""
        try:
            # One writer per company at a time: two concurrent copies must not both add the same person.
            self._session.execute(_LOCK, {"key": f"company-workers:{company_id}"})
            counts = [run() for run in statements]
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return counts[0] if counts else 0

    def _enroll(self, company_id: UUID, project_id: Optional[UUID], person_id: Optional[UUID]) -> int:
        params = {"company_id": company_id, "project_id": project_id, "person_id": person_id}
        return self._write(company_id, [lambda: int(self._session.execute(_ENROLL, params).scalar() or 0)])

    def enroll_person(self, project_id: UUID, person_id: UUID) -> int:
        company_id = self._company_of(project_id)
        return 0 if company_id is None else self._enroll(company_id, None, person_id)

    def enroll_company_workers_in_project(self, project_id: UUID) -> int:
        company_id = self._company_of(project_id)
        return 0 if company_id is None else self._enroll(company_id, project_id, None)

    def person_project_ids(self, project_id: UUID, person_id: UUID) -> list[UUID]:
        company_id = self._company_of(project_id)
        if company_id is None:
            return []
        rows = self._session.execute(_PERSON_PROJECTS, {"company_id": company_id, "person_id": person_id})
        return [UUID(str(r[0])) for r in rows]

    def set_person_active(
        self, project_id: UUID, person_id: UUID, active: bool, only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        company_id = self._company_of(project_id)
        if company_id is None:
            return 0
        stmt = _SET_ACTIVE.bindparams(bindparam("only_projects", type_=ARRAY(PG_UUID(as_uuid=False))))
        params = {
            "active": active,
            "person_id": person_id,
            "company_id": company_id,
            "only_projects": _uuid_list(only_projects),
        }
        return self._write(company_id, [lambda: self._session.execute(stmt, params).rowcount or 0])

    def set_person_role(
        self, project_id: UUID, person_id: UUID, role_id: Optional[UUID], only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        company_id = self._company_of(project_id)
        if company_id is None:
            return 0
        stmt = _SET_ROLE_ON_WORKERS.bindparams(bindparam("only_projects", type_=ARRAY(PG_UUID(as_uuid=False))))
        params = {
            "role_id": role_id,
            "person_id": person_id,
            "company_id": company_id,
            "only_projects": _uuid_list(only_projects),
        }
        profile = {"role_id": role_id, "person_id": person_id, "company_id": company_id}
        return self._write(
            company_id,
            [
                lambda: self._session.execute(stmt, params).rowcount or 0,
                lambda: self._session.execute(_SET_ROLE_ON_PROFILE, profile).rowcount or 0,
            ],
        )

    def sync_company(self, company_id: UUID) -> int:
        return self._enroll(company_id, None, None)
