"""`_can_invite` asks the resolver for `project:invite` on the target project.

There is no owner bypass and no legacy global/membership role fallback: a
company admin holds it on every project of their company, an assigned manager on
theirs, a member only through a D8 grant. Without a reader wired the check fails
closed (the route in front of the use-case has already made the same call).
"""

from types import SimpleNamespace
from uuid import uuid4

from app.application.invitations.create_invitation_usecase import CreateInvitationUseCase


class FakeReader:
    def __init__(self, *, role, company_id, project_id, assigned=True, grants=()):
        self._role = role
        self._company_id = company_id
        self._project_id = project_id
        self._assigned = assigned
        self._grants = list(grants)

    def company_role_for(self, user_id, company_id):
        return self._role if company_id == self._company_id else None

    def is_assigned(self, user_id, project_id):
        return self._assigned

    def project_company_id(self, project_id):
        return self._company_id if project_id == self._project_id else None

    def grants_for(self, user_id, company_id, project_id):
        return list(self._grants)

    def primary_company_id(self, user_id):
        return self._company_id

    def admin_company_ids(self, user_id):
        return [self._company_id] if self._role == "admin" else []

    def company_roles_for(self, user_id):
        return [(self._company_id, self._role)]

    def is_platform_ops(self, user_id):
        return False

    def has_project_assignment_in_company(self, user_id, company_id):
        return self._assigned

    def project_ids_for_company(self, company_id):
        return [self._project_id]

    def assigned_project_ids(self, user_id, project_ids):
        return list(project_ids) if self._assigned else []

    def assigned_project_ids_for_users(self, company_id, user_ids):
        return {}


def _usecase(reader=None):
    return CreateInvitationUseCase(
        invitation_repo=None,
        project_membership_repo=SimpleNamespace(find_role_id=lambda uid, pid: None),
        user_repo=None,
        project_repo=None,
        role_repo=SimpleNamespace(find_by_id=lambda rid: None),
        email_port=None,
        email_renderer=None,
        queue_port=None,
        app_base_url="http://x",
        db_session=None,
        authz_reader=reader,
    )


def _call(reader, *, inviter_id, project_id, owner_id=None):
    return _usecase(reader)._can_invite(None, owner_id or uuid4(), inviter_id, project_id)


def test_company_admin_may_invite():
    company_id, project_id, inviter = uuid4(), uuid4(), uuid4()
    reader = FakeReader(role="admin", company_id=company_id, project_id=project_id, assigned=False)
    assert _call(reader, inviter_id=inviter, project_id=project_id) is True


def test_assigned_manager_may_invite():
    company_id, project_id, inviter = uuid4(), uuid4(), uuid4()
    reader = FakeReader(role="manager", company_id=company_id, project_id=project_id)
    assert _call(reader, inviter_id=inviter, project_id=project_id) is True


def test_unassigned_manager_may_not():
    company_id, project_id, inviter = uuid4(), uuid4(), uuid4()
    reader = FakeReader(role="manager", company_id=company_id, project_id=project_id, assigned=False)
    assert _call(reader, inviter_id=inviter, project_id=project_id) is False


def test_member_may_not_unless_granted():
    company_id, project_id, inviter = uuid4(), uuid4(), uuid4()
    reader = FakeReader(role="member", company_id=company_id, project_id=project_id)
    assert _call(reader, inviter_id=inviter, project_id=project_id) is False

    granted = FakeReader(
        role="member",
        company_id=company_id,
        project_id=project_id,
        grants=[("project:invite", "grant")],
    )
    assert _call(granted, inviter_id=inviter, project_id=project_id) is True


def test_owner_without_a_company_role_may_not():
    """D6: owning the project is not a permission."""
    company_id, project_id, inviter = uuid4(), uuid4(), uuid4()
    reader = FakeReader(role=None, company_id=company_id, project_id=project_id)
    assert _call(reader, inviter_id=inviter, project_id=project_id, owner_id=inviter) is False


def test_without_a_reader_it_fails_closed():
    assert _call(None, inviter_id=uuid4(), project_id=uuid4()) is False
