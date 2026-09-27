"""assert_project_read_access: company admins read every project of their own company."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.billing.ports import assert_project_read_access
from app.domain.billing.exceptions import ForbiddenProjectAccessError
from app.domain.companies.user_company_access import UserCompanyAccess


class _Projects:
    def __init__(self, project):
        self._project = project

    def find_by_id(self, project_id):
        return self._project if project_id == self._project.id else None


class _Access:
    def __init__(self, rows):
        self._rows = {(r.user_id, r.company_id): r for r in rows}

    def find(self, user_id, company_id):
        return self._rows.get((user_id, company_id))


def _access(user_id, company_id, role):
    return UserCompanyAccess(
        user_id=user_id, company_id=company_id, is_primary=True, attached_at=datetime.now(timezone.utc), role=role
    )


@pytest.fixture
def project():
    return SimpleNamespace(id=uuid4(), owner_id=uuid4(), user_ids=[], company_id=uuid4())


def test_admin_of_the_project_company_passes(project):
    user = uuid4()
    access = _Access([_access(user, project.company_id, "admin")])
    assert_project_read_access(_Projects(project), project.id, user, access)


@pytest.mark.parametrize("role", ["manager", "member"])
def test_unassigned_manager_or_member_is_refused(project, role):
    user = uuid4()
    access = _Access([_access(user, project.company_id, role)])
    with pytest.raises(ForbiddenProjectAccessError):
        assert_project_read_access(_Projects(project), project.id, user, access)


def test_admin_of_another_company_is_refused(project):
    user = uuid4()
    access = _Access([_access(user, uuid4(), "admin")])
    with pytest.raises(ForbiddenProjectAccessError):
        assert_project_read_access(_Projects(project), project.id, user, access)


def test_assigned_member_still_passes_without_access_repo(project):
    user = uuid4()
    project.user_ids.append(user)
    assert_project_read_access(_Projects(project), project.id, user)
