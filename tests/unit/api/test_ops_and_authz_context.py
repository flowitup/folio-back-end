"""Per-request authz plumbing: the ops flag and the resolver memo.

Both modules must degrade — never raise — outside a request, without an
identity, or when the container has no reader wired.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask

import app.api.v1.authz_context as authz_context
import app.api.v1.ops_context as ops_context


class _Reader:
    def __init__(self, ops=False):
        self.ops = ops
        self.calls = 0

    def is_platform_ops(self, user_id):
        self.calls += 1
        return self.ops

    def company_role_for(self, user_id, company_id):
        return "admin"

    def is_assigned(self, user_id, project_id):
        return True

    def project_company_id(self, project_id):
        return uuid4()

    def grants_for(self, user_id, company_id, project_id):
        return []

    def admin_company_ids(self, user_id):
        return []

    def primary_company_id(self, user_id):
        return None

    def company_roles_for(self, user_id):
        return []

    def has_project_assignment_in_company(self, user_id, company_id):
        return True


def _container(monkeypatch, reader):
    import wiring

    monkeypatch.setattr(wiring, "get_container", lambda: SimpleNamespace(authz_reader=reader))


@pytest.fixture
def flask_app():
    return Flask(__name__)


class TestOpsContext:
    def test_outside_a_request_it_is_false(self, monkeypatch):
        _container(monkeypatch, _Reader(ops=True))
        assert ops_context.is_platform_ops() is False

    def test_missing_or_malformed_identity_is_false(self, monkeypatch):
        _container(monkeypatch, _Reader(ops=True))
        monkeypatch.setattr(ops_context, "get_jwt_identity", lambda: None)
        assert ops_context.is_platform_ops() is False
        assert ops_context.is_platform_ops("not-a-uuid") is False

    def test_without_a_reader_it_is_false(self, monkeypatch):
        _container(monkeypatch, None)
        assert ops_context.is_platform_ops(uuid4()) is False

    def test_reads_the_flag_for_an_explicit_user(self, monkeypatch):
        reader = _Reader(ops=True)
        _container(monkeypatch, reader)
        assert ops_context.is_platform_ops(uuid4()) is True
        assert ops_context.is_platform_ops(str(uuid4())) is True


class TestAuthzContext:
    def test_reader_cache_is_unshared_outside_an_app_context(self):
        first = authz_context.get_reader_cache()
        first["x"] = 1
        assert authz_context.get_reader_cache() == {}

    def test_clear_request_memo_outside_an_app_context_is_a_noop(self):
        authz_context.clear_request_memo()  # must not raise

    def test_memo_is_per_app_context_and_cleared(self, flask_app, monkeypatch):
        _container(monkeypatch, _Reader())
        user_id, project_id = uuid4(), uuid4()
        with flask_app.app_context():
            first = authz_context.resolve_for_request(user_id, project_id=project_id)
            again = authz_context.resolve_for_request(user_id, project_id=project_id)
            assert first is again  # memoized, same object
            authz_context.clear_request_memo()
            assert authz_context.resolve_for_request(user_id, project_id=project_id) == first

    def test_without_a_reader_it_degrades(self, flask_app, monkeypatch):
        _container(monkeypatch, None)
        with flask_app.app_context():
            assert authz_context.resolve_for_request(uuid4()) == frozenset()
            assert authz_context.resolve_for_request(uuid4(), is_platform_admin=True) == frozenset({"*:*"})
