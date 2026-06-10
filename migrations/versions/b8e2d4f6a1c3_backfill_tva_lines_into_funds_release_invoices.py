"""Backfill TVA lines into auto-generated released_funds invoices.

Released-funds expenses auto-created when a facture transitions to PAID copied
only the HT line items (description/quantity/unit_price — vat_rate dropped), so
their totals were missing the VAT and did not match the facture's TTC amount.

This migration regenerates the items of every auto-generated released_funds
invoice still linked to its source billing document: the copied HT lines plus
one "TVA {rate}%" line per VAT-rate bucket (amount quantized to cents,
ROUND_HALF_UP — matching how rendered totals are displayed).

Idempotent: items are fully regenerated from the source billing document, so
re-running produces the same result. Reversible: downgrade regenerates the same
items without the TVA lines (the pre-fix HT-only state).

Revision ID: b8e2d4f6a1c3
Revises: c4e8a2f1d3b6
Create Date: 2026-06-10 13:30:00.000000
"""

import json
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON, text
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "b8e2d4f6a1c3"
down_revision = "c4e8a2f1d3b6"
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, generic JSON elsewhere — mirrors the ORM column type.
_ItemsJSON = JSON().with_variant(JSONB(), "postgresql")


def _copied_ht_lines(billing_items: list) -> list:
    """Copy billing line items into expense item dicts (floats, no VAT)."""
    return [
        {
            "description": it.get("description", ""),
            "quantity": float(Decimal(str(it.get("quantity", 1)))),
            "unit_price": float(Decimal(str(it.get("unit_price", 0)))),
        }
        for it in billing_items
    ]


def _tva_lines(billing_items: list) -> list:
    """One TVA line per VAT-rate bucket, amounts quantized to cents."""
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


def _rewrite_items(include_tva: bool) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT i.id AS invoice_id, b.items AS billing_items
            FROM invoices i
            JOIN billing_documents b ON b.id = i.source_billing_document_id
            WHERE i.type = 'released_funds' AND i.is_auto_generated = true
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
        items = _copied_ht_lines(billing_items or [])
        if include_tva:
            items += _tva_lines(billing_items or [])
        bind.execute(update_stmt, {"items": items, "invoice_id": row.invoice_id})


def upgrade() -> None:
    _rewrite_items(include_tva=True)


def downgrade() -> None:
    _rewrite_items(include_tva=False)
