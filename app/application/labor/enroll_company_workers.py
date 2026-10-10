"""Keep every project of a company showing the company's whole worker list.

Workers are stored per project (attendance, pay and invoices hang off that row), but
the people belong to the company: a worker added on one site is listed on all of them,
and a new site starts with everyone. Rows are only ever added, never changed or
removed, and a person already on a project's roster — active or not — is left alone.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
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

    def sync_company(self, company_id: UUID) -> int:
        return self._roster.sync_company(company_id)

    @staticmethod
    def _safely(action, project_id: UUID) -> int:
        try:
            return action()
        except Exception:
            _log.exception("Company worker roster sync failed for project %s", project_id)
            return 0
