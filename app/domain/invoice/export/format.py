"""Invoice export formatting helpers — re-export labor helpers + invoice-specific bits.

Reuses format_eur_fr and slugify_project_name from app.domain.labor.export.format verbatim
(proven, fr-FR locked). Invoice-specific helpers (e.g. format_invoice_number) live here
when needed in future.
"""

from __future__ import annotations

from app.domain.labor.export.format import format_eur_fr, slugify_project_name  # noqa: F401

TYPE_LABEL_EN = {
    "released_funds": "Released Funds",
    "labor": "Labor",
    "materials_services": "Materials & Services",
    "others": "Others",
    "return": "Return",
}


def invoice_type_label(inv) -> str:
    """Row label for an exported invoice: its ledger type, flagged when it is a cash advance.

    A cash advance is listed under Others (see Invoice.ledger_type); the suffix keeps it
    distinguishable from a real Others expense for whoever reads the export.
    """
    value = inv.ledger_type.value
    label = TYPE_LABEL_EN.get(value, value.title())
    return f"{label} (cash advance)" if inv.is_cash_advance else label
