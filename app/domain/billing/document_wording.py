"""French wording shared by the billing document PDF and XLSX renderers.

Both renderers print the same sentences; keeping them here stops the two
files drifting apart.
"""

from __future__ import annotations

import re
from typing import Optional

from app.domain.billing.enums import BillingDocumentKind


def intro_sentence(kind: BillingDocumentKind) -> str:
    """Opening line under 'Madame, Monsieur,' — 'facture' is feminine, 'devis' masculine."""
    subject = "la facture relative" if kind == BillingDocumentKind.FACTURE else "le devis relatif"
    return f"Veuillez trouver ci-après {subject} à la mission citée en objet."


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
