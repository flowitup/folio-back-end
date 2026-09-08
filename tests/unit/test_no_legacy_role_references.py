"""The legacy RBAC vocabulary must not come back into the runtime code.

Permissions are derived from the company role plus grant/deny rows, and the
tables that used to hold them are dropped by migration c2b8f1a0d743. Anything
in `app/`, `scripts/` or `wiring.py` that still names them would either crash
at runtime or quietly gate on something that no longer exists.

`labor_roles` and `workers.role_id` are a different feature (the trade a worker
practises) and stay — hence the deliberately narrow patterns below.

`migrations/` is out of scope on purpose: revisions that ran before the drop
must keep reading those tables. `scripts/migration_legacy_role_mapping.py` is
the one scanned-tree file exempted for that reason — it is the data step of
revision 9a4c1e7b2d05 and never runs after the drop.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"

# Runtime code that must stay free of the vocabulary, and the one exemption.
SCANNED_ROOTS = (APP_ROOT, REPO_ROOT / "scripts")
SCANNED_FILES = (REPO_ROOT / "wiring.py",)
EXEMPT = (REPO_ROOT / "scripts" / "migration_legacy_role_mapping.py",)

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
    sources = [p for root in SCANNED_ROOTS for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    sources.extend(p for p in SCANNED_FILES if p.exists())
    return [p for p in sources if p not in EXEMPT]


@pytest.mark.parametrize("label,pattern", FORBIDDEN, ids=[label for label, _ in FORBIDDEN])
def test_no_legacy_role_reference_in_runtime_code(label: str, pattern: re.Pattern[str]) -> None:
    offenders: list[str] = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "LaborRole" in line or "labor_role" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, f"legacy {label} reference(s) left in runtime code:\n" + "\n".join(offenders)


def test_the_scan_covers_scripts_and_wiring() -> None:
    """Guard the guard: widening the tree is only useful if it is really scanned."""
    scanned = _python_sources()
    assert (REPO_ROOT / "scripts" / "seed_auth.py") in scanned
    assert (REPO_ROOT / "wiring.py") in scanned
    # The migration data step keeps its legacy SQL and must stay exempt.
    assert EXEMPT[0].exists() and EXEMPT[0] not in scanned


def test_labor_roles_are_untouched() -> None:
    """Guard the guard: the patterns above must not be so broad they ban labor roles."""
    labor_role_model = APP_ROOT / "infrastructure" / "database" / "models" / "labor_role.py"
    assert labor_role_model.exists()

    worker_model = (APP_ROOT / "infrastructure" / "database" / "models" / "worker.py").read_text(encoding="utf-8")
    assert "role_id" in worker_model, "workers.role_id is the labor role and must survive"


def test_the_roles_endpoint_is_gone() -> None:
    # The package, not the directory: a stale __pycache__/ can outlive `git rm`.
    assert not (APP_ROOT / "api" / "v1" / "roles" / "__init__.py").exists()
    assert not (APP_ROOT / "infrastructure" / "database" / "repositories" / "sqlalchemy_role.py").exists()
    assert not (APP_ROOT / "domain" / "entities" / "role.py").exists()
    assert not (APP_ROOT / "domain" / "entities" / "permission.py").exists()
