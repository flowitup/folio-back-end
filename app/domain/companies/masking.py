"""Sensitive-field masking helper for the companies bounded context.

Non-admin callers receive Company objects with sensitive fields replaced
by a masked representation. PDF rendering and admin endpoints bypass
masking by passing full=True.

Masked format: "····" + last 4 chars, or "····" when value is too short.
"""

from __future__ import annotations

from app.domain.companies.company import Company

# Fields on Company that must be masked for non-admin callers.
SENSITIVE_FIELDS: tuple[str, ...] = ("siret", "tva_number", "iban", "bic")

_BULLET = "····"


def _mask(value: str | None) -> str | None:
    """Mask a single sensitive string value.

    Rules:
      - None  → None  (field absent, nothing to mask)
      - len ≤ 4 → "····"  (too short to show tail)
      - len > 4 → "····" + value[-4:]
    """
    if value is None:
        return None
    if len(value) <= 4:
        return _BULLET
    return _BULLET + value[-4:]


def mask_company(company: Company, *, full: bool) -> Company:
    """Return a Company safe to expose to the caller.

    Args:
        company: The raw company entity (always holds unmasked values).
        full: When True (admin callers, PDF rendering) the original
              entity is returned unchanged. When False each field listed
              in SENSITIVE_FIELDS is replaced with its masked form.

    Returns:
        The original ``company`` instance when ``full=True``, otherwise a
        new frozen Company with sensitive fields replaced.
    """
    if full:
        return company

    masked_kwargs = {field: _mask(getattr(company, field)) for field in SENSITIVE_FIELDS}
    return company.with_updates(**masked_kwargs)
