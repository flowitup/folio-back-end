"""Shared helpers for the companies application layer.

Private module — only imported by use-cases in this package.
No Flask / SQLAlchemy / infrastructure imports allowed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.domain.companies.company import Company
from app.domain.companies.exceptions import ForbiddenCompanyError

_TOKEN_EXPIRY_DAYS = 7
_PREFIX_PATTERN = re.compile(r"^[A-Z0-9]{1,8}$")

_ADMIN_PERMISSION = "*:*"


def _now_utc() -> datetime:
    """Return the current UTC-aware datetime.

    Use-cases that already have a ClockPort should call clock.now() directly.
    This helper is for one-off use when no port is injected.
    """
    return datetime.now(timezone.utc)


def _assert_admin(caller_id: UUID, company_id: UUID, is_admin: bool) -> None:
    """Raise ForbiddenCompanyError if caller is not an admin.

    Args:
        caller_id: The UUID of the requesting user.
        company_id: The company being acted on (included in error context).
        is_admin: Pre-resolved from RoleCheckerPort.has_permission(caller_id, '*:*').
    """
    if not is_admin:
        raise ForbiddenCompanyError(caller_id, company_id)


def _validate_prefix_override(prefix_override: Optional[str]) -> None:
    """Raise ValueError if prefix_override does not match [A-Z0-9]{1,8}."""
    if prefix_override is not None and not _PREFIX_PATTERN.match(prefix_override):
        raise ValueError(f"prefix_override must match ^[A-Z0-9]{{1,8}}$, got: {prefix_override!r}")


def _validate_legal_name(legal_name: str) -> str:
    """Strip and validate legal_name. Raises ValueError if empty."""
    name = legal_name.strip()
    if not name:
        raise ValueError("legal_name is required and cannot be blank")
    return name


def _validate_address(address: str) -> str:
    """Strip and validate address. Raises ValueError if empty."""
    addr = address.strip()
    if not addr:
        raise ValueError("address is required and cannot be blank")
    return addr


def _snapshot_company_issuer(company: Company) -> dict:
    """Copy all issuer-relevant fields from Company by value.

    Returns a plain dict ready to be unpacked into BillingDocument kwargs.
    All values are immutable scalars — this is effectively a deep copy.
    """
    return {
        "issuer_legal_name": company.legal_name,
        "issuer_address": company.address,
        "issuer_siret": company.siret,
        "issuer_tva_number": company.tva_number,
        "issuer_iban": company.iban,
        "issuer_bic": company.bic,
        "issuer_logo_url": company.logo_url,
    }
