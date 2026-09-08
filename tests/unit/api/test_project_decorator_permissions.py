"""Project decorators resolve permissions through the company matrix only.

The legacy union (global JWT claim ∪ per-project membership role) is gone: what
a caller may do on a project comes from their company role, their assignment and
their D8 grant/deny rows, plus the `users.is_platform_ops` support flag. These
tests drive the decorators directly with a fake `AuthzReaderPort`, so they cover
the wildcard handling, the URL-resolution paths (`project_id`, `invoice_id`,
`task_id`, `attachment_id`) and the resource-aware write gate without a database.
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


class FakeReader:
    """AuthzReaderPort double: one company, one project, explicit role/grants."""

    def __init__(self, *, user_id, project_id, company_id, role, assigned=True, grants=(), ops=False):
        self._user_id = user_id
        self._project_id = project_id
        self._company_id = company_id
        self._role = role
        self._assigned = assigned
        self._grants = list(grants)
        self._ops = ops

    def company_role_for(self, user_id, company_id):
        return self._role if (user_id == self._user_id and company_id == self._company_id) else None

    def is_assigned(self, user_id, project_id):
        return self._assigned and user_id == self._user_id and project_id == self._project_id

    def project_company_id(self, project_id):
        return self._company_id if project_id == self._project_id else None

    def primary_company_id(self, user_id):
        return self._company_id

    def admin_company_ids(self, user_id):
        return [self._company_id] if self._role == "admin" else []

    def company_roles_for(self, user_id):
        return [(self._company_id, self._role)]

    def is_platform_ops(self, user_id):
        return self._ops

    def has_project_assignment_in_company(self, user_id, company_id):
        return self._assigned

    def grants_for(self, user_id, company_id, project_id):
        return list(self._grants)

    def project_ids_for_company(self, company_id):
        return [self._project_id]

    def assigned_project_ids(self, user_id, project_ids):
        return list(project_ids) if self._assigned else []

    def assigned_project_ids_for_users(self, company_id, user_ids):
        return {}


def _wire(monkeypatch, reader, user_id, *, invoice=None, task=None, attachment=None):
    """Point the decorators at `reader` and a caller identity."""
    import wiring

    container = SimpleNamespace(
        authz_reader=reader,
        invoice_repository=SimpleNamespace(find_by_id=lambda iid: invoice),
        task_repository=SimpleNamespace(find_by_id=lambda tid: task),
        invoice_attachment_repository=SimpleNamespace(find_by_id=lambda aid: attachment),
        project_repository=SimpleNamespace(find_by_id=lambda pid: None),
    )
    monkeypatch.setattr(wiring, "get_container", lambda: container)
    monkeypatch.setattr(dec, "get_jwt_identity", lambda: str(user_id))
    # The ops flag is read through its own module, with its own JWT import.
    import app.api.v1.ops_context as ops_context

    monkeypatch.setattr(ops_context, "get_jwt_identity", lambda: str(user_id))


def _ctx(role="manager", **kwargs):
    user_id, project_id, company_id = uuid4(), uuid4(), uuid4()
    reader = FakeReader(user_id=user_id, project_id=project_id, company_id=company_id, role=role, **kwargs)
    return reader, user_id, project_id


# ---------------------------------------------------------------------------
# require_permission
# ---------------------------------------------------------------------------


def test_assigned_manager_passes_a_project_permission(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:manage_labor")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_unassigned_manager_is_forbidden(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx(assigned=False)
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:manage_labor")
    def view(project_id):
        return "ok"

    body, status = view(project_id=str(project_id))
    assert status == 403
    assert "project:manage_labor" in body.get_json()["message"]


def test_member_grant_unlocks_one_permission(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx(role="member", grants=[("project:manage_invoices", "grant")])
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:manage_invoices")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_deny_row_beats_the_matrix(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx(grants=[("project:manage_labor", "deny")])
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:manage_labor")
    def view(project_id):
        return "ok"

    _body, status = view(project_id=str(project_id))
    assert status == 403


def test_platform_ops_passes_everything(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx(role="member", assigned=False, ops=True)
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:manage_invoices")
    def view(project_id):
        return "ok"

    assert view(project_id=str(project_id)) == "ok"


def test_unknown_project_resolves_to_no_permission(monkeypatch, app_ctx):
    reader, user_id, _project_id = _ctx()
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:read")
    def view(project_id):
        return "ok"

    _body, status = view(project_id=str(uuid4()))
    assert status == 403


def test_non_project_route_answers_from_company_roles(monkeypatch, app_ctx):
    """`project:create` for a company admin, refused for a manager."""
    reader, user_id, _pid = _ctx(role="admin")
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:create")
    def create():
        return "ok"

    assert create() == "ok"

    reader, user_id, _pid = _ctx(role="manager")
    _wire(monkeypatch, reader, user_id)
    _body, status = create()
    assert status == 403


def test_permission_resolved_through_an_invoice_id(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire(monkeypatch, reader, user_id, invoice=SimpleNamespace(project_id=project_id))

    @dec.require_permission("project:manage_invoices")
    def view(invoice_id):
        return "ok"

    assert view(invoice_id=str(uuid4())) == "ok"


def test_permission_resolved_through_a_task_id(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire(monkeypatch, reader, user_id, task=SimpleNamespace(project_id=project_id))

    @dec.require_permission("project:update")
    def view(task_id):
        return "ok"

    assert view(task_id=str(uuid4())) == "ok"


def test_permission_resolved_through_an_attachment_id(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    invoice_id = uuid4()
    _wire(
        monkeypatch,
        reader,
        user_id,
        invoice=SimpleNamespace(project_id=project_id),
        attachment=SimpleNamespace(invoice_id=invoice_id),
    )

    @dec.require_permission("project:manage_invoices")
    def view(attachment_id):
        return "ok"

    assert view(attachment_id=str(uuid4())) == "ok"


def test_malformed_identity_grants_nothing(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire(monkeypatch, reader, user_id)
    monkeypatch.setattr(dec, "get_jwt_identity", lambda: "not-a-uuid")

    @dec.require_permission("project:read")
    def view(project_id):
        return "ok"

    _body, status = view(project_id=str(project_id))
    assert status == 403


# ---------------------------------------------------------------------------
# can_read_project / can_mutate_project
# ---------------------------------------------------------------------------


def test_owner_without_a_company_role_cannot_read(monkeypatch):
    """D6: the owner column is not a bypass any more."""
    reader, user_id, project_id = _ctx(role="manager", assigned=False)
    _wire(monkeypatch, reader, user_id)
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[user_id])
    assert dec.can_read_project(project, user_id) is False


def test_assigned_member_can_read_but_not_mutate(monkeypatch):
    reader, user_id, project_id = _ctx(role="member")
    _wire(monkeypatch, reader, user_id)
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[user_id])
    assert dec.can_read_project(project, user_id) is True
    assert dec.can_mutate_project(project, user_id) is False


def test_write_gate_is_resource_aware(monkeypatch):
    """A grant of `manage_invoices` unlocks invoice writes only."""
    reader, user_id, project_id = _ctx(role="member", grants=[("project:manage_invoices", "grant")])
    _wire(monkeypatch, reader, user_id)
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[user_id])
    assert dec.can_mutate_project(project, user_id, "project:manage_invoices") is True
    assert dec.can_mutate_project(project, user_id, "project:update") is False


def test_manager_cannot_delete_a_project(monkeypatch):
    """D2: deletion is admin-only."""
    reader, user_id, project_id = _ctx(role="manager")
    _wire(monkeypatch, reader, user_id)
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[user_id])
    assert dec.can_mutate_project(project, user_id, "project:update") is True
    assert dec.can_mutate_project(project, user_id, "project:delete") is False


def test_company_admin_mutates_without_an_assignment(monkeypatch):
    reader, user_id, project_id = _ctx(role="admin", assigned=False)
    _wire(monkeypatch, reader, user_id)
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[])
    assert dec.can_read_project(project, user_id) is True
    assert dec.can_mutate_project(project, user_id, "project:delete") is True


# ---------------------------------------------------------------------------
# Access decorators: id validation, 404s, and the company-admin read fallback
# ---------------------------------------------------------------------------


def _wire_project(monkeypatch, reader, user_id, project, **kwargs):
    """Like `_wire`, but with a project repository that returns `project`."""
    import wiring

    container = SimpleNamespace(
        authz_reader=reader,
        project_repository=SimpleNamespace(find_by_id=lambda pid: project),
        invoice_repository=SimpleNamespace(find_by_id=lambda iid: kwargs.get("invoice")),
        task_repository=SimpleNamespace(find_by_id=lambda tid: kwargs.get("task")),
        invoice_attachment_repository=SimpleNamespace(find_by_id=lambda aid: kwargs.get("attachment")),
        user_company_access_repo=None,
    )
    monkeypatch.setattr(wiring, "get_container", lambda: container)
    monkeypatch.setattr(dec, "get_jwt_identity", lambda: str(user_id))
    import app.api.v1.ops_context as ops_context

    monkeypatch.setattr(ops_context, "get_jwt_identity", lambda: str(user_id))


def _view(decorator):
    @decorator
    def view(**kwargs):
        return "ok"

    return view


def test_resource_wildcard_matches():
    assert dec._has_permission(["project:*"], "project:read") is True
    assert dec._has_permission(["labor:*"], "project:read") is False


def test_resolve_project_id_rejects_a_malformed_id(monkeypatch):
    reader, user_id, _pid = _ctx()
    _wire(monkeypatch, reader, user_id)
    assert dec._resolve_project_id({"project_id": "not-a-uuid"}) is None
    assert dec._resolve_project_id({}) is None


def test_resolve_project_id_through_a_missing_entity(monkeypatch):
    reader, user_id, _pid = _ctx()
    _wire(monkeypatch, reader, user_id)  # every repo returns None
    assert dec._resolve_project_id({"invoice_id": str(uuid4())}) is None
    assert dec._resolve_project_id({"task_id": str(uuid4())}) is None
    assert dec._resolve_project_id({"attachment_id": str(uuid4())}) is None
    assert dec._resolve_project_id({"attachment_id": "not-a-uuid"}) is None


def test_require_project_access_validates_the_id(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire_project(monkeypatch, reader, user_id, SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[]))
    view = _view(dec.require_project_access())

    _body, status = view()
    assert status == 403  # missing project id
    _body, status = view(project_id="not-a-uuid")
    assert status == 403


def test_require_project_access_404s_and_403s(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    _wire_project(monkeypatch, reader, user_id, None)
    _body, status = _view(dec.require_project_access())(project_id=str(project_id))
    assert status == 404

    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    reader, user_id, project_id = _ctx(role="member")
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    _wire_project(monkeypatch, reader, user_id, project)
    assert _view(dec.require_project_access())(project_id=str(project_id)) == "ok"
    _body, status = _view(dec.require_project_access(write=True))(project_id=str(project_id))
    assert status == 403


def test_require_invoice_access_paths(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    invoice = SimpleNamespace(project_id=project_id)

    _wire_project(monkeypatch, reader, user_id, project, invoice=invoice)
    assert _view(dec.require_invoice_access())(invoice_id=str(uuid4())) == "ok"
    assert _view(dec.require_invoice_access(write=True))(invoice_id=str(uuid4())) == "ok"

    _body, status = _view(dec.require_invoice_access())()
    assert status == 403  # missing invoice id
    _body, status = _view(dec.require_invoice_access())(invoice_id="not-a-uuid")
    assert status == 403

    _wire_project(monkeypatch, reader, user_id, project, invoice=None)
    _body, status = _view(dec.require_invoice_access())(invoice_id=str(uuid4()))
    assert status == 404

    _wire_project(monkeypatch, reader, user_id, None, invoice=invoice)
    _body, status = _view(dec.require_invoice_access())(invoice_id=str(uuid4()))
    assert status == 404


def test_require_task_access_paths(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    task = SimpleNamespace(project_id=project_id)

    _wire_project(monkeypatch, reader, user_id, project, task=task)
    assert _view(dec.require_task_access())(task_id=str(uuid4())) == "ok"
    assert _view(dec.require_task_access(write=True))(task_id=str(uuid4())) == "ok"

    _body, status = _view(dec.require_task_access())()
    assert status == 403
    _body, status = _view(dec.require_task_access())(task_id="not-a-uuid")
    assert status == 403

    _wire_project(monkeypatch, reader, user_id, project, task=None)
    _body, status = _view(dec.require_task_access())(task_id=str(uuid4()))
    assert status == 404

    _wire_project(monkeypatch, reader, user_id, None, task=task)
    _body, status = _view(dec.require_task_access())(task_id=str(uuid4()))
    assert status == 404

    reader, user_id, project_id = _ctx(role="member")
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[])
    _wire_project(monkeypatch, reader, user_id, project, task=SimpleNamespace(project_id=project_id))
    _body, status = _view(dec.require_task_access(write=True))(task_id=str(uuid4()))
    assert status == 403


def test_require_attachment_access_paths(monkeypatch, app_ctx):
    reader, user_id, project_id = _ctx()
    project = SimpleNamespace(id=project_id, owner_id=user_id, user_ids=[])
    invoice = SimpleNamespace(project_id=project_id)
    attachment = SimpleNamespace(invoice_id=uuid4())

    _wire_project(monkeypatch, reader, user_id, project, invoice=invoice, attachment=attachment)
    assert _view(dec.require_attachment_access())(attachment_id=str(uuid4())) == "ok"
    assert _view(dec.require_attachment_access(write=True))(attachment_id=str(uuid4())) == "ok"

    _body, status = _view(dec.require_attachment_access())()
    assert status == 403
    _body, status = _view(dec.require_attachment_access())(attachment_id="not-a-uuid")
    assert status == 403

    _wire_project(monkeypatch, reader, user_id, project, invoice=invoice, attachment=None)
    _body, status = _view(dec.require_attachment_access())(attachment_id=str(uuid4()))
    assert status == 404

    _wire_project(monkeypatch, reader, user_id, project, invoice=None, attachment=attachment)
    _body, status = _view(dec.require_attachment_access())(attachment_id=str(uuid4()))
    assert status == 404

    _wire_project(monkeypatch, reader, user_id, None, invoice=invoice, attachment=attachment)
    _body, status = _view(dec.require_attachment_access())(attachment_id=str(uuid4()))
    assert status == 404


def test_company_admin_reads_an_invoice_without_an_assignment(monkeypatch, app_ctx):
    """A company admin reads their company's invoices through the matrix alone.

    The old `allow_company_admin` escape hatch is gone: `project:read` for an
    admin comes from the resolver, assigned or not.
    """
    reader, user_id, project_id = _ctx(role="admin", assigned=False)
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[])
    invoice = SimpleNamespace(project_id=project_id)
    _wire_project(monkeypatch, reader, user_id, project, invoice=invoice)

    assert _view(dec.require_invoice_access())(invoice_id=str(uuid4())) == "ok"


def test_unassigned_member_cannot_read_an_invoice(monkeypatch, app_ctx):
    """No company-admin fallback for a plain member of the project's company."""
    reader, user_id, project_id = _ctx(role="member", assigned=False)
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[])
    invoice = SimpleNamespace(project_id=project_id)
    _wire_project(monkeypatch, reader, user_id, project, invoice=invoice)

    _body, status = _view(dec.require_invoice_access())(invoice_id=str(uuid4()))
    assert status == 403


# ---------------------------------------------------------------------------
# 404 before 403: an id nobody can act on is "not found", not "forbidden"
# ---------------------------------------------------------------------------


class _ReaderWithExistence(FakeReader):
    """FakeReader that also answers `project_exists` (the real reader does)."""

    def project_exists(self, project_id):
        return project_id == self._project_id


def test_unknown_project_id_is_404_before_the_permission_check(monkeypatch, app_ctx):
    user_id, project_id, company_id = uuid4(), uuid4(), uuid4()
    reader = _ReaderWithExistence(user_id=user_id, project_id=project_id, company_id=company_id, role="admin")
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:read")
    def view(project_id):  # pragma: no cover - never reached
        return "ok"

    _body, status = view(project_id=str(uuid4()))
    assert status == 404
    assert view(project_id=str(project_id)) == "ok"


def test_existing_project_the_caller_cannot_read_is_403(monkeypatch, app_ctx):
    user_id, project_id, company_id = uuid4(), uuid4(), uuid4()
    reader = _ReaderWithExistence(
        user_id=user_id, project_id=project_id, company_id=company_id, role="member", assigned=False
    )
    _wire(monkeypatch, reader, user_id)

    @dec.require_permission("project:read")
    def view(project_id):  # pragma: no cover - never reached
        return "ok"

    _body, status = view(project_id=str(project_id))
    assert status == 403


def test_missing_invoice_answers_404_from_require_permission(monkeypatch, app_ctx):
    reader, user_id, _project_id = _ctx()
    _wire(monkeypatch, reader, user_id, invoice=None)

    @dec.require_permission("project:read")
    def view(invoice_id):  # pragma: no cover - never reached
        return "ok"

    _body, status = view(invoice_id=str(uuid4()))
    assert status == 404


def test_uuid_converter_kwargs_are_accepted(monkeypatch, app_ctx):
    """Routes declared with `<uuid:project_id>` hand the decorators a UUID object."""
    reader, user_id, project_id = _ctx()
    project = SimpleNamespace(id=project_id, owner_id=uuid4(), user_ids=[])
    _wire_project(monkeypatch, reader, user_id, project)

    @dec.require_permission("project:read")
    @dec.require_project_access()
    def view(project_id):
        return "ok"

    assert view(project_id=project_id) == "ok"
