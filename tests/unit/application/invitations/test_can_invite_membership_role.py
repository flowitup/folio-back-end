"""_can_invite honors the inviter's per-project membership role.

A user invited as a project manager/admin (global role = read-only default) must
be able to invite to that project via their membership-role permissions, not only
via a global role or project ownership.
"""

from types import SimpleNamespace
from uuid import uuid4

from app.application.invitations.create_invitation_usecase import CreateInvitationUseCase


def _usecase(*, membership_role_id=None, role=None):
    return CreateInvitationUseCase(
        invitation_repo=None,
        project_membership_repo=SimpleNamespace(find_role_id=lambda uid, pid: membership_role_id),
        user_repo=None,
        project_repo=None,
        role_repo=SimpleNamespace(find_by_id=lambda rid: role),
        email_port=None,
        email_renderer=None,
        queue_port=None,
        app_base_url="http://x",
        db_session=None,
    )


def _user(*global_pairs):
    pairs = set(global_pairs)
    return SimpleNamespace(has_permission=lambda r, a: (r, a) in pairs or ("*", "*") in pairs)


def _role(*perm_names):
    return SimpleNamespace(permissions=[SimpleNamespace(name=n) for n in perm_names])


def test_owner_can_invite():
    uc = _usecase()
    owner = uuid4()
    assert uc._can_invite(_user(), owner, owner, uuid4()) is True


def test_global_project_invite_can_invite():
    uc = _usecase()
    assert uc._can_invite(_user(("project", "invite")), uuid4(), uuid4(), uuid4()) is True


def test_membership_role_with_invite_can_invite():
    """Global role lacks invite, but the project-membership role grants it."""
    uc = _usecase(membership_role_id=uuid4(), role=_role("project:read", "project:invite"))
    assert uc._can_invite(_user(), uuid4(), uuid4(), uuid4()) is True


def test_membership_admin_wildcard_can_invite():
    uc = _usecase(membership_role_id=uuid4(), role=_role("*:*"))
    assert uc._can_invite(_user(), uuid4(), uuid4(), uuid4()) is True


def test_read_only_membership_cannot_invite():
    uc = _usecase(membership_role_id=uuid4(), role=_role("project:read", "user:read"))
    assert uc._can_invite(_user(), uuid4(), uuid4(), uuid4()) is False


def test_non_member_non_owner_cannot_invite():
    uc = _usecase(membership_role_id=None)
    assert uc._can_invite(_user(), uuid4(), uuid4(), uuid4()) is False
