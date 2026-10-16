"""Locale-aware bits shared by the push / bell texts (amounts, plurals, role names).

Push and bell texts are rendered once per locale (``render(locale)``), so anything that
reads differently per language — a money amount, a plural, a role — is formatted here
rather than frozen into one string for every locale.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from app.domain.labor.export.format import format_eur_fr


def format_amount(value: object, locale: str) -> str:
    """An EUR amount as the apps print it.

    fr and vi read like the rest of the app (fr-FR money everywhere): ``1 234,56 €``.
    en reads ``€1,234.56``. Empty for a missing or unreadable amount.
    """
    if value is None:
        return ""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""
    if not amount.is_finite():
        return ""
    if locale == "en":
        cents = amount.quantize(Decimal("0.01"))
        sign = "-" if cents < 0 else ""
        return f"{sign}€{abs(cents):,.2f}"
    return format_eur_fr(amount)


# Company roles, one word per language, the same everywhere the apps name them.
ROLE_LABELS: Dict[str, Dict[str, str]] = {
    "vi": {"admin": "Quản trị viên", "manager": "Quản lý", "member": "Thành viên"},
    "fr": {"admin": "Admin", "manager": "Manager", "member": "Membre"},
    "en": {"admin": "Admin", "manager": "Manager", "member": "Member"},
}


def role_label(role: Optional[str], locale: str) -> str:
    """The localized company role name, or the raw value when it is not a known role."""
    if not role:
        return ""
    return ROLE_LABELS.get(locale, ROLE_LABELS["en"]).get(role, role)


def looks_like_phone(name: Optional[str]) -> bool:
    """True for a name that is only a phone number (a person added by phone without a name)."""
    if not name:
        return False
    compact = name.replace(" ", "").replace(".", "").replace("-", "")
    if compact.startswith("+"):
        return compact[1:].isdigit()
    return compact.isdigit() and len(compact) >= 8
