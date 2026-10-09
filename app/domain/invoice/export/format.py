"""Invoice export formatting helpers — re-export labor helpers + invoice-specific bits.

Reuses format_eur_fr and slugify_project_name from app.domain.labor.export.format verbatim
(proven, fr-FR locked). Invoice-specific helpers (e.g. format_invoice_number) live here
when needed in future.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.domain.entities.invoice import InvoiceType
from app.domain.invoice.export.labels import DEFAULT_LOCALE, t, type_label, type_labels
from app.domain.labor.export.format import format_eur_fr, format_generated_at, slugify_project_name  # noqa: F401
from app.domain.labor.export.labels import month_label  # noqa: F401

TYPE_LABEL_EN = type_labels("en")

# The line of the release created for a bank refund is stored in French
# (app/infrastructure/adapters/funds_release_adapter.py); it is translated where it is shown.
BANK_REFUND_LINE_PREFIX = "Remboursement banque — "


def round_cents(value: Decimal) -> Decimal:
    """A money amount rounded half-up to the cent, as the API serialises it (dtos.money)."""
    if not value.is_finite():
        return value
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, value.adjusted() + 3)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _flagged_type_label(invoice_type: InvoiceType, is_cash_advance: bool, locale: str) -> str:
    label = type_label(invoice_type.value, locale)
    return f"{label} ({t(locale, 'cash_advance')})" if is_cash_advance else label


def invoice_type_label(inv, locale: str = DEFAULT_LOCALE) -> str:
    """Row label for an exported invoice: its ledger type, flagged when it is a cash advance.

    A cash advance is listed under Others (see Invoice.ledger_type); the suffix keeps it
    distinguishable from a real Others expense for whoever reads the export.
    """
    return _flagged_type_label(inv.ledger_type, inv.is_cash_advance, locale)


def subtotal_type_label(sub, locale: str = DEFAULT_LOCALE) -> str:
    """Label of a subtotal row, flagged the same way when it sums cash advances."""
    return _flagged_type_label(sub.type, sub.is_cash_advance, locale)


def invoice_item_label(inv, description: str, locale: str = DEFAULT_LOCALE) -> str:
    """An item's description in `locale`: the auto bank-refund line is translated, the rest is as typed."""
    if (
        inv.type == InvoiceType.RELEASED_FUNDS
        and inv.is_auto_generated
        and description.startswith(BANK_REFUND_LINE_PREFIX)
    ):
        return t(locale, "bank_refund_line", number=description[len(BANK_REFUND_LINE_PREFIX) :])
    return description
