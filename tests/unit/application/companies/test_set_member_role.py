"""Unit tests for SetMemberRoleUseCase (admin promotes/demotes company members)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.application.companies.dtos import SetMemberRoleInput
from app.application.companies.set_member_role_usecase import SetMemberRoleUseCase
from app.domain.companies.exceptions import (
    ForbiddenCompanyError,
    LastCompanyAdminError,
    UserCompanyAccessNotFoundError,
)
from app.domain.companies.user_company_access import UserCompanyAccess


def _access(user_id, company_id, role, is_primary=True):
    return UserCompanyAccess(
        user_id=user_id,
        company_id=company_id,
        is_primary=is_primary,
        attached_at=datetime.now(timezone.utc),
        role=role,
    )


@pytest.fixture
def usecase(access_repo, role_service):
    return SetMemberRoleUseCase(access_repo=access_repo, role_checker=role_service)


class TestSetMemberRole:
    def test_promote_member_to_admin(self, usecase, access_repo, fake_session, admin_id, user_id):
        company_id = uuid4()
        access_repo.save(_access(user_id, company_id, "member"))

        result = usecase.execute(
            SetMemberRoleInput(caller_id=admin_id, company_id=company_id, user_id=user_id, role="admin"),
            fake_session,
        )

        assert result.role == "admin"
        assert access_repo.find(user_id, company_id).role == "admin"

    def test_demote_admin_when_another_admin_exists(self, usecase, access_repo, fake_session, admin_id, user_id):
        company_id = uuid4()
        access_repo.save(_access(user_id, company_id, "admin"))
        access_repo.save(_access(uuid4(), company_id, "admin", is_primary=False))

        result = usecase.execute(
            SetMemberRoleInput(caller_id=admin_id, company_id=company_id, user_id=user_id, role="member"),
            fake_session,
        )

        assert result.role == "member"

    def test_demoting_last_admin_is_rejected(self, usecase, access_repo, fake_session, admin_id, user_id):
        company_id = uuid4()
        access_repo.save(_access(user_id, company_id, "admin"))

        with pytest.raises(LastCompanyAdminError):
            usecase.execute(
                SetMemberRoleInput(caller_id=admin_id, company_id=company_id, user_id=user_id, role="member"),
                fake_session,
            )

    def test_target_not_attached_raises(self, usecase, fake_session, admin_id, user_id):
        with pytest.raises(UserCompanyAccessNotFoundError):
            usecase.execute(
                SetMemberRoleInput(caller_id=admin_id, company_id=uuid4(), user_id=user_id, role="admin"),
                fake_session,
            )

    def test_invalid_role_raises(self, usecase, access_repo, fake_session, admin_id, user_id):
        company_id = uuid4()
        access_repo.save(_access(user_id, company_id, "member"))
        with pytest.raises(ValueError):
            usecase.execute(
                SetMemberRoleInput(caller_id=admin_id, company_id=company_id, user_id=user_id, role="owner"),
                fake_session,
            )

    def test_non_admin_caller_forbidden(self, usecase, access_repo, fake_session, user_id):
        company_id = uuid4()
        access_repo.save(_access(user_id, company_id, "member"))
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(
                SetMemberRoleInput(caller_id=uuid4(), company_id=company_id, user_id=user_id, role="admin"),
                fake_session,
            )
