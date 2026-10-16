"""French wording shared by the billing document PDF and XLSX renderers.

Both renderers print the same sentences; keeping them here stops the two
files drifting apart.
"""

from __future__ import annotations

import re
from typing import Optional

from app.domain.billing.enums import BillingDocumentKind


def intro_sentence(kind: BillingDocumentKind, has_object: bool = True) -> str:
    """Opening line under 'Madame, Monsieur,' — 'facture' is feminine, 'devis' masculine.

    It refers to "la mission citée en objet" only when an 'Objet' line (the
    linked project) is printed; otherwise the sentence is neutral.
    """
    if not has_object:
        noun = "notre facture" if kind == BillingDocumentKind.FACTURE else "notre devis"
        return f"Veuillez trouver ci-après {noun}."
    subject = "la facture relative" if kind == BillingDocumentKind.FACTURE else "le devis relatif"
    return f"Veuillez trouver ci-après {subject} à la mission citée en objet."


# Late-payment mention printed on factures (same wording on the PDF and the XLSX).
LATE_PAYMENT_NOTE = (
    "Indemnité forfaitaire de retard de paiement : 40 € "
    "(conformément à l'article 121-II de la loi n° 2012-387 du 22 Mars 2012 "
    "et au décret n° 2012-1115 du 2 Oct. 2012)."
)


def format_iban(iban: Optional[str]) -> str:
    """IBAN in groups of four ('FR76 3000 6000 …'), the way it is printed on paper."""
    compact = "".join((iban or "").split())
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


_POSTCODE_CITY = re.compile(r"\b\d{5}\s+(\D.*)$")
_POSTCODE_ONLY = re.compile(r"^\d{4,5}$")
_COUNTRIES = {"france", "belgique", "belgium", "suisse", "switzerland", "luxembourg", "vietnam", "viet nam"}


def place_of_issue(address: Optional[str]) -> str:
    """City for the '<City>, DD/MM/YYYY' line, read from a free-text company address.

    Anchored on the French postcode: '9 rue X, 75011 Paris, France' gives
    'Paris'. A trailing country is ignored, a postcode on its own segment
    takes the neighbouring segment, and when no city can be found the answer
    is '' so the line shows the date alone rather than a wrong place.
    """
    if not address:
        return ""
    segments = [p.strip() for p in re.split(r"[,\n]", address) if p.strip()]
    segments = [p for p in segments if p.lower() not in _COUNTRIES]
    for i, segment in enumerate(segments):
        match = _POSTCODE_CITY.search(segment)
        if match:
            return match.group(1).strip()
        if _POSTCODE_ONLY.match(segment):
            neighbours = segments[i + 1 : i + 2] + segments[max(i - 1, 0) : i]
            for candidate in neighbours:
                if not any(ch.isdigit() for ch in candidate):
                    return candidate
    return ""
