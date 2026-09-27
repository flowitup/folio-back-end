"""Invoice export formatting helpers — re-export labor helpers + invoice-specific bits.

Reuses format_eur_fr and slugify_project_name from app.domain.labor.export.format verbatim
(proven, fr-FR locked). Invoice-specific helpers (e.g. format_invoice_number) live here
when needed in future.
"""

from __future__ import annotations

from app.domain.invoice.export.labels import DEFAULT_LOCALE, t, type_label, type_labels
from app.domain.labor.export.format import format_eur_fr, slugify_project_name  # noqa: F401

TYPE_LABEL_EN = type_labels("en")


def invoice_type_label(inv, locale: str = DEFAULT_LOCALE) -> str:
    """Row label for an exported invoice: its ledger type, flagged when it is a cash advance.

    A cash advance is listed under Others (see Invoice.ledger_type); the suffix keeps it
    distinguishable from a real Others expense for whoever reads the export.
    """
    label = type_label(inv.ledger_type.value, locale)
    return f"{label} ({t(locale, 'cash_advance')})" if inv.is_cash_advance else label
