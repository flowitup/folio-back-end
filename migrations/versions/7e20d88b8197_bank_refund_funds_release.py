"""Bank-refund funds release: partial unique index + historical backfill.

Enforces the invariant that every materials_services invoice with
refundable_status='refunded' AND refunded_by IN ('bank', 'both') has exactly
one auto-generated released_funds invoice linked to it via refunds_invoice_id.
The live path (SetInvoiceRefundableStatusUseCase, via FundsReleaseAdapter)
creates/deletes these going forward; this migration backfills every
historical row that predates the feature.

Enum values are compared as text (type::text = '...') in every DML predicate
and cast explicitly on insert ('released_funds'::invoicetype): PostgreSQL
forbids using a bare enum literal against an enum column inside the same
transaction that introduced the value, and a fresh database runs the whole
migration chain in one transaction (see 3c8eef064050 for the precedent).

Revision ID: 7e20d88b8197
Revises: a1d4f7b20c93
Create Date: 2026-07-28 00:00:00.000000
"""

import json
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7e20d88b8197"
down_revision = "a1d4f7b20c93"
branch_labels = None
depends_on = None


def _items_total(items: list | None) -> Decimal:
    """TTC total of a raw JSON items list: Sum(qty * unit_price * (1 + vat/100)).

    Copied verbatim from app.infrastructure.database.invoice_spend_rules.items_total
    (formerly the private _items_total helper in sqlalchemy_invoice.py) rather than
    imported: migrations must stay correct independent of later application
    refactors, so the money math is frozen here at the point this revision was
    written.
    """
    total = Decimal("0")
    for it in items or []:
        qty = Decimal(str(it.get("quantity", 0)))
        price = Decimal(str(it.get("unit_price", 0)))
        vat = Decimal(str(it.get("vat_rate", 0)))
        total += qty * price * (1 + vat / Decimal("100"))
    return total


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Partial unique index FIRST so the backfill below cannot itself create
    #    (or race a concurrent write into creating) a duplicate release for
    #    the same source — mirrors the live-path adapter's idempotency guard
    #    at the DB level.
    op.create_index(
        "uq_invoices_bank_refund_release",
        "invoices",
        ["refunds_invoice_id"],
        unique=True,
        postgresql_where=sa.text("type = 'released_funds' AND refunds_invoice_id IS NOT NULL"),
    )

    # 2. Every historical bank/both-refunded materials_services row with no
    #    linked release yet. Deterministic ORDER BY so FR numbering is
    #    reproducible across environments and re-runs.
    sources = bind.execute(
        sa.text(
            """
            SELECT i.id, i.project_id, i.invoice_number, i.issue_date,
                   i.recipient_name, i.items, i.created_by
            FROM invoices i
            WHERE i.type::text = 'materials_services'
              AND i.refundable_status = 'refunded'
              AND i.refunded_by IN ('bank', 'both')
              AND NOT EXISTS (
                  SELECT 1 FROM invoices r
                  WHERE r.type::text = 'released_funds' AND r.refunds_invoice_id = i.id
              )
            ORDER BY i.project_id, i.issue_date, i.invoice_number
            """
        )
    ).fetchall()

    # 3. Seed FR counters per (project_id, year) from existing maxima so the
    #    backfill continues the exact sequence the live path would produce.
    #    invoice_number shape is 'FR-YYYY-NNNN'; substring(4 for 4) = 'YYYY'.
    counter_rows = bind.execute(
        sa.text(
            """
            SELECT project_id,
                   substring(invoice_number from 4 for 4) AS yr,
                   MAX(invoice_number) AS max_number
            FROM invoices
            WHERE invoice_number LIKE 'FR-%'
            GROUP BY project_id, substring(invoice_number from 4 for 4)
            """
        )
    ).fetchall()

    counters: dict[tuple, int] = {}
    for row in counter_rows:
        try:
            counters[(row.project_id, row.yr)] = int(row.max_number.rsplit("-", 1)[-1])
        except (ValueError, IndexError, AttributeError):
            continue

    inserted = 0
    skipped_non_positive = 0

    for src in sources:
        total = _items_total(src.items)
        if total <= 0:
            skipped_non_positive += 1
            continue

        year = src.issue_date.year
        key = (src.project_id, f"{year:04d}")
        next_seq = counters.get(key, 0) + 1
        counters[key] = next_seq
        fr_number = f"FR-{year:04d}-{next_seq:04d}"

        items_payload = json.dumps(
            [
                {
                    "description": f"Remboursement banque — {src.invoice_number}",
                    "quantity": 1.0,
                    "unit_price": float(total),
                    "vat_rate": 0.0,
                }
            ]
        )

        bind.execute(
            sa.text(
                """
                INSERT INTO invoices (
                    id, project_id, invoice_number, type, issue_date, recipient_name,
                    items, created_by, created_at, updated_at,
                    refunds_invoice_id, is_auto_generated
                )
                VALUES (
                    gen_random_uuid(), :project_id, :invoice_number,
                    'released_funds'::invoicetype, :issue_date, :recipient_name,
                    CAST(:items AS jsonb), :created_by, now(), now(),
                    :source_id, true
                )
                """
            ),
            {
                "project_id": src.project_id,
                "invoice_number": fr_number,
                "issue_date": src.issue_date,
                "recipient_name": src.recipient_name,
                "items": items_payload,
                "created_by": src.created_by,
                "source_id": src.id,
            },
        )
        inserted += 1

    print(
        f"[bank_refund_funds_release] sources found={len(sources)} "
        f"inserted={inserted} skipped_non_positive={skipped_non_positive}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Precise: facture-driven releases always have refunds_invoice_id IS NULL,
    # so they never match this predicate and survive the downgrade untouched.
    bind.execute(
        sa.text(
            """
            DELETE FROM invoices
            WHERE type::text = 'released_funds'
              AND is_auto_generated IS TRUE
              AND refunds_invoice_id IS NOT NULL
            """
        )
    )
    op.drop_index("uq_invoices_bank_refund_release", table_name="invoices")
