"""Fail-fast guard against multiple Alembic head revisions.

Scans ``migrations/versions/*.py`` for ``revision = "..."`` and
``down_revision = ...`` declarations, builds the parent → child graph,
and exits non-zero when more than one terminal head exists.

Two heads break `flask db upgrade head` at deploy time — which is what
bit prod when migrations b8e2d1c47a90 (avatar_url) and b1c2d3e4f5a6
(persons + company-scoping) both declared down_revision a3f7b8c9d0e1.
Catching this in CI is much cheaper than catching it in prod.

Runs as plain Python — no Alembic / Flask context required, so it works
on a bare CI runner before deps are installed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"

# Captures `revision = "abc123"` and `down_revision = "abc123"` (or None,
# or a tuple for merges). Tuples are rare; we accept them but only the
# first element is treated as the parent for the head calculation. The
# graph-walk below treats `None` (root) and tuples (merge nodes) safely.
REVISION_RE = re.compile(r'^revision\s*=\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)
DOWN_REVISION_TUPLE_RE = re.compile(r"^down_revision\s*=\s*\(([^)]+)\)", re.MULTILINE)
DOWN_REVISION_RE = re.compile(r'^down_revision\s*=\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)
DOWN_REVISION_NONE_RE = re.compile(r"^down_revision\s*=\s*None", re.MULTILINE)


def parse_migration(path: Path) -> tuple[str, list[str]]:
    """Return (revision, [parent_revisions]) for a single migration file.

    ``parents`` is empty for root migrations (down_revision = None) and
    has one element for the normal case. Merge migrations list multiple
    parents in a tuple — we return all of them.
    """
    text = path.read_text(encoding="utf-8")

    rev_match = REVISION_RE.search(text)
    if not rev_match:
        raise RuntimeError(f'{path}: no `revision = "..."` line found')
    revision = rev_match.group(1)

    if DOWN_REVISION_NONE_RE.search(text):
        return revision, []

    tuple_match = DOWN_REVISION_TUPLE_RE.search(text)
    if tuple_match:
        parents = re.findall(r'[\'"]([^\'"]+)[\'"]', tuple_match.group(1))
        return revision, parents

    single_match = DOWN_REVISION_RE.search(text)
    if single_match:
        return revision, [single_match.group(1)]

    raise RuntimeError(f"{path}: no `down_revision = ...` line found")


def main() -> int:
    if not VERSIONS_DIR.is_dir():
        print(f"ERROR: migrations directory not found at {VERSIONS_DIR}", file=sys.stderr)
        return 2

    revisions: dict[str, list[str]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        rev, parents = parse_migration(path)
        if rev in revisions:
            print(f"ERROR: duplicate revision id {rev} in {path}", file=sys.stderr)
            return 2
        revisions[rev] = parents

    referenced = {parent for parents in revisions.values() for parent in parents}
    heads = sorted(rev for rev in revisions if rev not in referenced)

    if len(heads) <= 1:
        head = heads[0] if heads else "(empty)"
        print(f"OK: single migration head — {head}")
        return 0

    print("ERROR: multiple migration heads detected", file=sys.stderr)
    for head in heads:
        # Find the file containing this revision so the diagnostic
        # points the reader at something they can edit.
        for path in VERSIONS_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(rf'^revision\s*=\s*[\'"]{re.escape(head)}[\'"]', text, re.MULTILINE):
                print(f"  - {head}  ({path.relative_to(REPO_ROOT)})", file=sys.stderr)
                break
    print(
        "\nFix: re-point one head's `down_revision` so the chain is linear, or\n"
        'create a merge migration with `flask db merge -m "merge heads" <h1> <h2>`.',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
