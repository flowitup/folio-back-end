"""French wording shared by the billing document PDF and XLSX renderers.

Both renderers print the same sentences; keeping them here stops the two
files drifting apart.
"""

from __future__ import annotations

from app.domain.billing.enums import BillingDocumentKind


def intro_sentence(kind: BillingDocumentKind) -> str:
    """Opening line under 'Madame, Monsieur,' — 'facture' is feminine, 'devis' masculine."""
    subject = "la facture relative" if kind == BillingDocumentKind.FACTURE else "le devis relatif"
    return f"Veuillez trouver ci-après {subject} à la mission citée en objet."
