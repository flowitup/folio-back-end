"""Restructure auto funds-release invoice items to carry per-line vat_rate.

Previous form (b8e2d4f6a1c3): items = HT lines + synthetic "TVA {rate}%"
aggregation lines (one per VAT-rate bucket, amount rounded to cents).

New form: items = billing document lines verbatim with vat_rate field;
no synthetic lines. The invoice TTC is preserved because each line's
total is quantity × unit_price × (1 + vat_rate/100).

Idempotent: items are fully regenerated from the source billing document
on each run. Only auto-generated released_funds invoices linked to a
billing document are affected; unlinked and manual rows are untouched.

Reversible: downgrade regenerates the b8e2d4f6a1c3 form (HT lines +
per-rate TVA lines, 2 dp ROUND_HALF_UP, sorted rate descending, zero
buckets skipped).

Precision note: per-line TTC sum and per-bucket-rounded TVA sum can
differ by ≤ 1 cent when unit_price × quantity has sub-cent precision.
At the standard rates present in real data (20%, 10%) the sums are
exact. Display rounding in the application layer handles any residual.

Revision ID: a1c3e5f7b2d4
Revises: b8e2d4f6a1c3
Create Date: 2026-06-10 16:30:00.000000
"""

import json
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON, text
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "a1c3e5f7b2d4"
down_revision = "b8e2d4f6a1c3"
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, generic JSON elsewhere — mirrors the ORM column type.
_ItemsJSON = JSON().with_variant(JSONB(), "postgresql")


def _structured_items(billing_items: list) -> list:
    """Convert billing document items to per-line structured expense items.

    Each item carries its own vat_rate so the expense TTC can be computed
    as quantity × unit_price × (1 + vat_rate/100) without synthetic lines.
    """
    return [
        {
            "description": it.get("description", ""),
            "quantity": float(Decimal(str(it.get("quantity", 1)))),
            "unit_price": float(Decimal(str(it.get("unit_price", 0)))),
            "vat_rate": float(Decimal(str(it.get("vat_rate", 0)))),
        }
        for it in billing_items
    ]


def _ht_lines(billing_items: list) -> list:
    """Copy billing line items into expense item dicts without VAT (legacy HT form)."""
    return [
        {
            "description": it.get("description", ""),
            "quantity": float(Decimal(str(it.get("quantity", 1)))),
            "unit_price": float(Decimal(str(it.get("unit_price", 0)))),
        }
        for it in billing_items
    ]


def _tva_lines(billing_items: list) -> list:
    """One TVA line per VAT-rate bucket, amounts quantized to cents (legacy form)."""
    tva_by_rate: dict = {}
    for it in billing_items:
        rate = Decimal(str(it.get("vat_rate", 0))).normalize()
        line_ht = Decimal(str(it.get("quantity", 1))) * Decimal(str(it.get("unit_price", 0)))
        tva_by_rate[rate] = tva_by_rate.get(rate, Decimal("0")) + line_ht * rate / Decimal("100")

    lines = []
    for rate in sorted(tva_by_rate, reverse=True):
        amount = tva_by_rate[rate].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount == 0:
            continue
        lines.append(
            {
                "description": f"TVA {rate:f}%",
                "quantity": 1.0,
                "unit_price": float(amount),
            }
        )
    return lines


def _rewrite_items(structured: bool) -> None:
    """Regenerate items for all auto-generated linked released_funds invoices.

    structured=True  → new per-line vat_rate form (no synthetic TVA lines)
    structured=False → legacy HT lines + TVA bucket lines (b8e2d4f6a1c3 form)
    """
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT i.id AS invoice_id, b.items AS billing_items
            FROM invoices i
            JOIN billing_documents b ON b.id = i.source_billing_document_id
            WHERE i.type = 'released_funds'
              AND i.is_auto_generated = true
              AND i.source_billing_document_id IS NOT NULL
            """
        )
    ).fetchall()

    update_stmt = text("UPDATE invoices SET items = :items WHERE id = :invoice_id").bindparams(
        sa.bindparam("items", type_=_ItemsJSON)
    )

    for row in rows:
        billing_items = row.billing_items
        if isinstance(billing_items, str):
            billing_items = json.loads(billing_items)
        billing_items = billing_items or []

        if structured:
            items = _structured_items(billing_items)
        else:
            items = _ht_lines(billing_items) + _tva_lines(billing_items)

        bind.execute(update_stmt, {"items": items, "invoice_id": row.invoice_id})


def upgrade() -> None:
    _rewrite_items(structured=True)


def downgrade() -> None:
    _rewrite_items(structured=False)
