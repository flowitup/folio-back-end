"""Keep every project of a company showing the company's whole worker list.

Workers are stored per project (attendance, pay and invoices hang off that row), but
the people belong to the company: a worker added on one site is listed on all of them,
and a new site starts with everyone. A copy takes the source row's base rate, its rate
timeline and its role, so the person costs the same per day everywhere. Rows are never
removed, and a person already on a project's roster — active or not — is not re-added.
A person stopped on every site stays stopped on sites created later until restarted.
A role change follows the person to their other sites (the role is their trade).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, Sequence
from uuid import UUID

_log = logging.getLogger(__name__)


class ICompanyWorkerRoster(ABC):
    """Port: copy workers onto the company projects that lack them."""

    @abstractmethod
    def enroll_person(self, project_id: UUID, person_id: UUID) -> int:
        """Add ``person_id`` to every other project of ``project_id``'s company. Returns rows added."""

    @abstractmethod
    def enroll_company_workers_in_project(self, project_id: UUID) -> int:
        """Add every active worker of the company to ``project_id``. Returns rows added."""

    @abstractmethod
    def person_project_ids(self, project_id: UUID, person_id: UUID) -> list[UUID]:
        """Projects of ``project_id``'s company where ``person_id`` has a worker row (active or not)."""

    @abstractmethod
    def set_person_active(
        self, project_id: UUID, person_id: UUID, active: bool, only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        """Stop or restart ``person_id`` on the projects of ``project_id``'s company (limited to
        ``only_projects`` when given). Returns rows changed."""

    @abstractmethod
    def set_person_role(
        self, project_id: UUID, person_id: UUID, role_id: Optional[UUID], only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        """Give ``person_id`` the role ``role_id`` on the projects of the company and in its directory."""

    @abstractmethod
    def sync_company(self, company_id: UUID) -> int:
        """Give every project of the company every active worker of the company. Returns rows added."""


class EnrollCompanyWorkersUseCase:
    """Fan workers out across a company's projects.

    Callers run this after their own change is saved, so a failure here is logged and
    swallowed: the worker or project the user just created must never fail because the
    copy to the other sites did.
    """

    def __init__(self, roster: ICompanyWorkerRoster):
        self._roster = roster

    def after_worker_created(self, project_id: UUID, person_id: UUID | None) -> int:
        if person_id is None:
            return 0
        return self._safely(lambda: self._roster.enroll_person(project_id, person_id), project_id)

    def after_project_created(self, project_id: UUID) -> int:
        return self._safely(lambda: self._roster.enroll_company_workers_in_project(project_id), project_id)

    def before_roster_listed(self, project_id: UUID) -> int:
        """Pick up people added to the company directory (or workers added elsewhere) since the last visit."""
        return self.after_project_created(project_id)

    def person_project_ids(self, project_id: UUID, person_id: UUID | None) -> list[UUID]:
        if person_id is None:
            return []
        try:
            return self._roster.person_project_ids(project_id, person_id)
        except Exception:
            _log.exception("Company worker roster lookup failed for project %s", project_id)
            return []

    def after_worker_stopped(
        self, project_id: UUID, person_id: UUID | None, only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        if person_id is None:
            return 0
        return self._safely(
            lambda: self._roster.set_person_active(project_id, person_id, False, only_projects), project_id
        )

    def after_worker_restarted(
        self, project_id: UUID, person_id: UUID | None, only_projects: Optional[Sequence[UUID]] = None
    ) -> int:
        if person_id is None:
            return 0
        changed = self._safely(
            lambda: self._roster.set_person_active(project_id, person_id, True, only_projects), project_id
        )
        # Sites created while the person was stopped did not get them: add them now.
        self._safely(lambda: self._roster.enroll_person(project_id, person_id), project_id)
        return changed

    def after_role_changed(
        self,
        project_id: UUID,
        person_id: UUID | None,
        role_id: UUID | None,
        only_projects: Optional[Sequence[UUID]] = None,
    ) -> int:
        if person_id is None:
            return 0
        return self._safely(
            lambda: self._roster.set_person_role(project_id, person_id, role_id, only_projects), project_id
        )

    def sync_company(self, company_id: UUID) -> int:
        return self._roster.sync_company(company_id)

    @staticmethod
    def _safely(action, project_id: UUID) -> int:
        try:
            return action()
        except Exception:
            _log.exception("Company worker roster sync failed for project %s", project_id)
            return 0
