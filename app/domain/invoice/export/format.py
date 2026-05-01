"""Invoice export formatting helpers — re-export labor helpers + invoice-specific bits.

Reuses format_eur_fr and slugify_project_name from app.domain.labor.export.format verbatim
(proven, fr-FR locked). Invoice-specific helpers (e.g. format_invoice_number) live here
when needed in future.
"""

from __future__ import annotations

from app.domain.labor.export.format import format_eur_fr, slugify_project_name  # noqa: F401

TYPE_LABEL_EN = {
    "client": "Client",
    "labor": "Labor",
    "supplier": "Supplier",
}
