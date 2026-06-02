"""Project-scoped permission resolution honors the caller's membership role.

Regression: invited users get the read-only default global role; the role picked
at invite time is stored only as their project-membership role. Permission checks
must union that membership role's permissions (scoped to the project) so a user
invited as a project admin/manager can manage labor/invoices on that project.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask

import app.api.v1.projects.decorators as dec


@pytest.fixture
def app_ctx():
    """Minimal app context so `jsonify` works in the 403 branch."""
    app = Flask(__name__)
    with app.app_context():
        yield app


def _perm(name):
    return SimpleNamespace(name=name)


def _role(*perm_names):
    return SimpleNamespace(permissions=[_perm(n) for n in perm_names])


def _patch_identity(monkeypatch, *, global_perms, user_id):
    monkeypatch.setattr(dec, "get_jwt", lambda: {"permissions": list(global_perms)})
    monkeypatch.setattr(dec, "get_jwt_identity", lambda: str(user_id))


def _patch_container(monkeypatch, *, membership_role_id=None, role=None, invoice=None, task=None, attachment=None):
    container = SimpleNamespace(
        project_membership_repo=SimpleNamespace(find_role_id=lambda uid, pid: membership_role_id),
        role_repository=SimpleNamespace(find_by_id=lambda rid: role),
        invoice_repository=SimpleNamespace(find_by_id=lambda iid: invoice),
        task_repository=SimpleNamespace(find_by_id=lambda tid: task),
        invoice_attachment_repository=SimpleNamespace(find_by_id=lambda aid: attachment),
    )
    # Both decorators import `get_container` locally via `from wiring import get_container`.
    import wiring

    monkeypatch.setattr(wiring, "get_container", lambda: container)
    return container


# ---------------------------------------------------------------------------
# _membership_role_permissions
# ---------------------------------------------------------------------------


def test_membership_role_permissions_returns_role_perms(monkeypatch):
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role("project:manage_labor", "project:read"))
    perms = dec._membership_role_permissions(uuid4(), uuid4())
    assert perms == {"project:manage_labor", "project:read"}


def test_membership_role_permissions_empty_when_not_member(monkeypatch):
    _patch_container(monkeypatch, membership_role_id=None)
    assert dec._membership_role_permissions(uuid4(), uuid4()) == set()


# ---------------------------------------------------------------------------
# require_permission (effective = global UNION membership)
# ---------------------------------------------------------------------------


def test_invited_admin_membership_passes_manage_labor(monkeypatch, app_ctx):
    """Global role 'user' (read-only) + membership role admin (*:*) -> allowed."""
    user_id, project_id = uuid4(), uuid4()
    _patch_identity(monkeypatch, global_perms=["project:read", "user:read"], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role("*:*"))

    @dec.require_permission("project:manage_labor")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_invited_manager_membership_passes_manage_invoices(monkeypatch, app_ctx):
    user_id, project_id = uuid4(), uuid4()
    _patch_identity(monkeypatch, global_perms=["project:read"], user_id=user_id)
    _patch_container(
        monkeypatch,
        membership_role_id=uuid4(),
        role=_role("project:manage_invoices", "project:read", "project:create"),
    )

    @dec.require_permission("project:manage_invoices")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_member_role_without_perm_is_forbidden(monkeypatch, app_ctx):
    """Global 'user' + membership role 'user' (read-only) -> 403 on manage_labor."""
    user_id, project_id = uuid4(), uuid4()
    _patch_identity(monkeypatch, global_perms=["project:read", "user:read"], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role("project:read", "user:read"))

    @dec.require_permission("project:manage_labor")
    def view(project_id):
        return "ok"

    body, status = view(project_id=str(project_id))
    assert status == 403
    assert "project:manage_labor" in body.get_json()["message"]


def test_global_admin_unaffected_no_regression(monkeypatch, app_ctx):
    """Global '*:*' passes even with no membership row (union only adds)."""
    user_id, project_id = uuid4(), uuid4()
    _patch_identity(monkeypatch, global_perms=["*:*"], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=None)

    @dec.require_permission("project:manage_invoices")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_non_project_route_uses_global_only(monkeypatch, app_ctx):
    """No project context in kwargs -> global perms only; membership never consulted."""
    user_id = uuid4()
    _patch_identity(monkeypatch, global_perms=["project:read"], user_id=user_id)
    # find_role_id would grant *:* if (wrongly) consulted — assert it is NOT.
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role("*:*"))

    @dec.require_permission("project:create")
    def create():
        return "ok"

    body, status = create()
    assert status == 403


def test_membership_perms_applied_via_invoice_id_resolution(monkeypatch, app_ctx):
    """Routes carrying only <invoice_id> resolve the project, then union membership."""
    user_id, project_id, invoice_id = uuid4(), uuid4(), uuid4()
    _patch_identity(monkeypatch, global_perms=["project:read"], user_id=user_id)
    _patch_container(
        monkeypatch,
        membership_role_id=uuid4(),
        role=_role("project:manage_invoices"),
        invoice=SimpleNamespace(project_id=project_id),
    )

    @dec.require_permission("project:manage_invoices")
    def view(invoice_id):
        return "ok"

    assert view(invoice_id=str(invoice_id)) == "ok"


# ---------------------------------------------------------------------------
# can_mutate_project / can_read_project honor membership role
# ---------------------------------------------------------------------------


def test_can_mutate_project_allows_membership_manager(monkeypatch):
    user_id, project_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[user_id])
    _patch_identity(monkeypatch, global_perms=["project:read"], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role("project:create", "project:manage_invoices"))
    assert dec.can_mutate_project(project, user_id) is True


def test_can_mutate_project_denies_membership_member(monkeypatch):
    user_id, project_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[user_id])
    _patch_identity(monkeypatch, global_perms=["project:read"], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=uuid4(), role=_role())  # 0-perm member role
    assert dec.can_mutate_project(project, user_id) is False


def test_can_mutate_project_allows_owner(monkeypatch):
    user_id, project_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    _patch_identity(monkeypatch, global_perms=[], user_id=user_id)
    _patch_container(monkeypatch, membership_role_id=None)
    assert dec.can_mutate_project(project, user_id) is True
