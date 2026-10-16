"""A write permission on a project the caller cannot read grants nothing (write without read)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.projects import decorators


@pytest.mark.parametrize(
    ("perms", "allowed"),
    [
        ({"project:update"}, False),  # D8 grant on a project the member is not assigned to
        ({"project:update", "project:read"}, True),
        ({"project:read"}, False),
    ],
)
def test_can_mutate_project_needs_read_too(monkeypatch, perms, allowed):
    monkeypatch.setattr(decorators, "_resolver_permissions_for_project", lambda user_id, project_id: perms)
    project = SimpleNamespace(id=uuid4())
    assert decorators.can_mutate_project(project, uuid4(), "project:update") is allowed
