"""The legacy RBAC vocabulary must not come back into `app/`.

Permissions are derived from the company role plus grant/deny rows, and the
tables that used to hold them are dropped by migration c2b8f1a0d743. Anything
in `app/` that still names them would either crash at runtime or quietly gate
on something that no longer exists.

`labor_roles` and `workers.role_id` are a different feature (the trade a worker
practises) and stay — hence the deliberately narrow patterns below.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

# (label, regex). Each must not match anywhere under app/.
FORBIDDEN = [
    ("RoleModel", re.compile(r"\bRoleModel\b(?<!LaborRoleModel)")),
    ("PermissionModel", re.compile(r"\bPermissionModel\b")),
    ("user_roles table", re.compile(r"\buser_roles\b")),
    ("role_permissions table", re.compile(r"\brole_permissions\b")),
    ("roles table", re.compile(r"\b(?:FROM|INTO|JOIN|UPDATE)\s+roles\b", re.IGNORECASE)),
    ("permissions table", re.compile(r"\b(?:FROM|INTO|JOIN|UPDATE)\s+permissions\b", re.IGNORECASE)),
    ("user_projects.role_id", re.compile(r"user_projects[^\n]*\brole_id\b")),
    ("invitations.role_id", re.compile(r"invitations[^\n]*\brole_id\b")),
]


def _python_sources() -> list[pathlib.Path]:
    return [p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("label,pattern", FORBIDDEN, ids=[label for label, _ in FORBIDDEN])
def test_no_legacy_role_reference_in_app(label: str, pattern: re.Pattern[str]) -> None:
    offenders: list[str] = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "LaborRole" in line or "labor_role" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{lineno}: {line.strip()}")
    assert not offenders, f"legacy {label} reference(s) left in app/:\n" + "\n".join(offenders)


def test_labor_roles_are_untouched() -> None:
    """Guard the guard: the patterns above must not be so broad they ban labor roles."""
    labor_role_model = APP_ROOT / "infrastructure" / "database" / "models" / "labor_role.py"
    assert labor_role_model.exists()

    worker_model = (APP_ROOT / "infrastructure" / "database" / "models" / "worker.py").read_text(encoding="utf-8")
    assert "role_id" in worker_model, "workers.role_id is the labor role and must survive"


def test_the_roles_endpoint_is_gone() -> None:
    assert not (APP_ROOT / "api" / "v1" / "roles").exists()
    assert not (APP_ROOT / "infrastructure" / "database" / "repositories" / "sqlalchemy_role.py").exists()
    assert not (APP_ROOT / "domain" / "entities" / "role.py").exists()
    assert not (APP_ROOT / "domain" / "entities" / "permission.py").exists()
