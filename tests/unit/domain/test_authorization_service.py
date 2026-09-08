"""AuthorizationService — the resolver-backed facade every use-case asks.

Legacy global roles are inert: what a user may do comes from their company role
(+ assignment + D8 rows), and the only bypass is `users.is_platform_ops`.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.domain.services.authorization import AuthorizationService


class FakeReader:
    """AuthzReaderPort double: `{company_id: role}` plus assignment/grant/ops switches."""

    def __init__(self, *, roles=None, primary=None, assigned=True, grants=(), ops=False):
        self._roles = roles or {}
        self._primary = primary
        self._assigned = assigned
        self._grants = list(grants)
        self._ops = ops

    def company_role_for(self, user_id, company_id):
        return self._roles.get(company_id)

    def is_assigned(self, user_id, project_id):
        return self._assigned

    def project_company_id(self, project_id):
        return None

    def primary_company_id(self, user_id):
        return self._primary

    def admin_company_ids(self, user_id):
        return [cid for cid, role in self._roles.items() if role == "admin"]

    def company_roles_for(self, user_id):
        return list(self._roles.items())

    def has_project_assignment_in_company(self, user_id, company_id):
        return self._assigned

    def is_platform_ops(self, user_id):
        return self._ops

    def grants_for(self, user_id, company_id, project_id):
        return list(self._grants)

    def project_ids_for_company(self, company_id):
        return []

    def assigned_project_ids(self, user_id, project_ids):
        return []

    def assigned_project_ids_for_users(self, company_id, user_ids):
        return {}


@pytest.fixture
def user_repo():
    return Mock()


def _service(user_repo, reader) -> AuthorizationService:
    service = AuthorizationService(user_repo)
    service.set_authz_reader(reader)
    return service


class TestPlatformOps:
    def test_flag_is_the_only_bypass(self, user_repo):
        service = _service(user_repo, FakeReader(ops=True))
        user_id = uuid4()
        assert service.is_platform_admin(user_id) is True
        assert service.has_permission(user_id, "company:manage_billing") is True
        assert service.get_user_permissions(user_id) == {"*:*"}

    def test_without_the_flag_and_without_a_company_nothing_is_granted(self, user_repo):
        service = _service(user_repo, FakeReader())
        user_id = uuid4()
        assert service.is_platform_admin(user_id) is False
        assert service.has_permission(user_id, "project:read") is False
        assert service.get_user_permissions(user_id) == set()

    def test_no_reader_fails_closed(self, user_repo):
        service = AuthorizationService(user_repo)
        user_id = uuid4()
        assert service.is_platform_admin(user_id) is False
        assert service.has_permission(user_id, "project:read") is False
        assert service.get_user_permissions(user_id) == set()


class TestCompanyRoles:
    def test_admin_holds_the_full_matrix(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "admin"}, primary=company_id))
        user_id = uuid4()
        assert service.is_company_admin(user_id, company_id) is True
        assert service.has_permission(user_id, "bibliotheque:manage") is True
        assert service.has_permission(user_id, "company:manage_members") is True
        assert "project:create" in service.get_user_permissions(user_id)

    def test_assigned_manager_manages_the_library_but_not_the_company(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "manager"}, primary=company_id))
        user_id = uuid4()
        assert service.is_company_admin(user_id, company_id) is False
        assert service.has_permission(user_id, "bibliotheque:manage") is True
        assert service.has_permission(user_id, "company:manage_settings") is False

    def test_manager_assigned_to_nothing_manages_nothing(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "manager"}, primary=company_id, assigned=False))
        assert service.has_permission(uuid4(), "bibliotheque:manage") is False

    def test_member_is_read_only(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "member"}, primary=company_id))
        user_id = uuid4()
        assert service.has_permission(user_id, "project:read") is True
        assert service.has_permission(user_id, "project:manage_invoices") is False

    def test_a_grant_in_any_company_answers_the_context_free_question(self, user_repo):
        company_id = uuid4()
        service = _service(
            user_repo,
            FakeReader(
                roles={company_id: "member"},
                primary=company_id,
                grants=[("bibliotheque:manage", "grant")],
            ),
        )
        assert service.has_permission(uuid4(), "bibliotheque:manage") is True

    def test_a_deny_removes_it_again(self, user_repo):
        company_id = uuid4()
        service = _service(
            user_repo,
            FakeReader(
                roles={company_id: "manager"},
                primary=company_id,
                grants=[("bibliotheque:manage", "deny")],
            ),
        )
        assert service.has_permission(uuid4(), "bibliotheque:manage") is False

    def test_permissions_follow_the_primary_company(self, user_repo):
        primary, other = uuid4(), uuid4()
        service = _service(user_repo, FakeReader(roles={primary: "member", other: "admin"}, primary=primary))
        user_id = uuid4()
        # The claim/`/auth/me` payload shows the primary company…
        assert "project:create" not in service.get_user_permissions(user_id)
        # …while a context-free capability question still finds the other company.
        assert service.has_permission(user_id, "project:create") is True

    def test_permission_in_company_is_scoped_to_that_company(self, user_repo):
        admin_company, member_company = uuid4(), uuid4()
        service = _service(
            user_repo,
            FakeReader(roles={admin_company: "admin", member_company: "member"}, primary=admin_company),
        )
        user_id = uuid4()
        assert service.has_permission_in_company(user_id, "company:manage_billing", admin_company) is True
        assert service.has_permission_in_company(user_id, "company:manage_billing", member_company) is False


class TestAnyAndAllPermissions:
    def test_any_and_all(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "member"}, primary=company_id))
        user_id = uuid4()
        assert service.has_any_permission(user_id, ["project:read", "project:delete"]) is True
        assert service.has_all_permissions(user_id, ["project:read", "project:delete"]) is False
        assert service.has_all_permissions(user_id, ["project:read", "user:read"]) is True


class TestHasRole:
    def test_company_role_and_ops(self, user_repo):
        company_id = uuid4()
        service = _service(user_repo, FakeReader(roles={company_id: "manager"}, primary=company_id))
        user_id = uuid4()
        assert service.has_role(user_id, "manager") is True
        assert service.has_role(user_id, "MANAGER") is True
        assert service.has_role(user_id, "admin") is False
        assert service.has_role(user_id, "ops") is False

        ops_service = _service(user_repo, FakeReader(ops=True))
        assert ops_service.has_role(user_id, "ops") is True
