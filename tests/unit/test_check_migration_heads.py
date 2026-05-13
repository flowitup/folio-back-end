"""Unit tests for scripts/check_migration_heads.py.

The script is invoked from CI to fail-fast when migrations/ has multiple
head revisions (the bug that took prod down after PR #42 + #41 both
pointed at the same parent). These tests pin its behaviour so a future
refactor of the script can't silently regress the guard.
"""

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_migration_heads.py"


def _write_migration(tmp_versions: Path, revision: str, down_revision: str | None) -> None:
    down_repr = "None" if down_revision is None else f'"{down_revision}"'
    body = f'''"""test migration."""

revision = "{revision}"
down_revision = {down_repr}
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
'''
    (tmp_versions / f"{revision}.py").write_text(body)


def _run(tmp_repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=tmp_repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthesise a minimal repo layout the script's REPO_ROOT logic sees.

    The script computes REPO_ROOT as ``script_dir.parent.parent`` from
    its own __file__. Re-pointing the script that way is fragile in
    tests, so instead we copy the script next to a temp ``scripts/``
    and create a sibling ``migrations/versions/`` tree.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "migrations" / "versions").mkdir(parents=True)
    # Symlink the real script so we test exactly the code under review.
    (tmp_path / "scripts" / "check_migration_heads.py").write_text(SCRIPT_PATH.read_text())
    return tmp_path


def test_single_head_passes(fake_repo: Path) -> None:
    versions = fake_repo / "migrations" / "versions"
    _write_migration(versions, "aaa", None)
    _write_migration(versions, "bbb", "aaa")
    _write_migration(versions, "ccc", "bbb")

    result = subprocess.run(
        [sys.executable, str(fake_repo / "scripts" / "check_migration_heads.py")],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "single migration head" in result.stdout
    assert "ccc" in result.stdout


def test_two_heads_fails(fake_repo: Path) -> None:
    versions = fake_repo / "migrations" / "versions"
    _write_migration(versions, "aaa", None)
    _write_migration(versions, "bbb", "aaa")
    _write_migration(versions, "ccc", "aaa")  # second head — same parent

    result = subprocess.run(
        [sys.executable, str(fake_repo / "scripts" / "check_migration_heads.py")],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "multiple migration heads detected" in result.stderr
    assert "bbb" in result.stderr
    assert "ccc" in result.stderr


def test_merge_migration_resolves_two_heads(fake_repo: Path) -> None:
    """A merge migration (tuple down_revision) collapses two heads."""
    versions = fake_repo / "migrations" / "versions"
    _write_migration(versions, "aaa", None)
    _write_migration(versions, "bbb", "aaa")
    _write_migration(versions, "ccc", "aaa")
    # Merge migration — tuple syntax for down_revision.
    (versions / "ddd.py").write_text(
        '''"""merge."""

revision = "ddd"
down_revision = ("bbb", "ccc")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
'''
    )

    result = subprocess.run(
        [sys.executable, str(fake_repo / "scripts" / "check_migration_heads.py")],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ddd" in result.stdout


def test_real_repo_passes() -> None:
    """The actual repo's migrations must always have a single head."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=SCRIPT_PATH.parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr={result.stderr}"
    assert "single migration head" in result.stdout
