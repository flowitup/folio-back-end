"""Backfill existing library product categories to canonical slugs.

This migration reads every distinct non-null category value from the
bibliotheque_products table and rewrites it to the canonical slug returned
by normalize_category(). Values that are already slugs are idempotent.
Values that cannot be mapped become "autre". NULL values are left as NULL.

No DDL change — the category column remains a plain VARCHAR.

Revision ID: a1c3e5f7b9d2
Revises: b7c1f2e3a4d5
Create Date: 2026-06-03 00:00:00.000000
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
# b7c1f2e3a4d5 is the single migration head on master; earlier divergent heads
# were already consolidated by prior merge revisions and are its ancestors.
revision = "a1c3e5f7b9d2"
down_revision = "b7c1f2e3a4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Map all existing free-text category values to canonical slugs.

    Steps:
    1. SELECT DISTINCT category WHERE category IS NOT NULL
    2. For each distinct raw value: compute slug = normalize_category(raw)
    3. If slug != raw: UPDATE rows with that raw value to the slug

    Idempotent: re-running after the migration is already applied is safe
    because every slug normalises to itself (no-op UPDATE when slug == raw).

    Side effect: a non-null but whitespace-only category (e.g. '' or '   ')
    folds to empty, so normalize_category returns None and the row is set to
    NULL (treated as genuinely uncategorised). This is intentional.
    """
    # Import here — pure stdlib module, no Flask/SQLAlchemy infra deps.
    from app.domain.value_objects.library_category import normalize_category

    bind = op.get_bind()

    # Fetch all distinct non-null raw category strings
    rows = bind.execute(
        text("SELECT DISTINCT category FROM bibliotheque_products WHERE category IS NOT NULL")
    ).fetchall()

    for (raw,) in rows:
        slug = normalize_category(raw)
        # Only issue an UPDATE when the slug differs from the stored value
        # (handles already-normalised rows without redundant writes)
        if slug != raw:
            bind.execute(
                text("UPDATE bibliotheque_products SET category = :slug WHERE category = :raw"),
                {"slug": slug, "raw": raw},
            )


def downgrade() -> None:
    """No-op — original free-text strings are intentionally not recoverable.

    NOTE: one-way normalization. The canonical-slug mapping is irreversible;
    the original free-text category labels (e.g. "Plomberie de salle de bain")
    cannot be reconstructed from the slug ("plomberie"). A full table backup
    taken before applying this migration is the only recovery path.
    """
    pass
