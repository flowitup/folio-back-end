"""Pydantic v2 request/response schemas for the companies API.

Strict mode (extra='forbid') enforced on all request schemas.
Logo URL validation uses SSRF-safe scheme + private-IP-block logic.
"""

from __future__ import annotations

import ipaddress
import socket
from decimal import Decimal
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# SSRF-safe logo URL validator (shared)
# ---------------------------------------------------------------------------


def _validate_logo_url(v: Optional[HttpUrl]) -> Optional[HttpUrl]:
    """Reject logo URLs that point to private / loopback IP ranges (SSRF guard).

    Allowed schemes: http, https only (HttpUrl already enforces this).
    Blocked: any hostname that resolves to RFC-1918, loopback, or link-local.
    We validate at the schema level; runtime re-validation is the caller's concern.
    """
    if v is None:
        return v
    host = v.host
    if host is None:
        raise ValueError("logo_url must have a valid hostname")
    # N8: HttpUrl already enforces http/https scheme; redundant check removed.
    # M5 (DNS-rebinding): BE validates once at save time; the FE/PDF renderer does
    # its own DNS lookup later which is a documented acceptable risk (same-host
    # SSRF surface is small since BE does not fetch the URL itself).
    # Attempt to resolve hostname; catch all DNS errors gracefully
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Cannot resolve — reject to be safe
        raise ValueError(f"logo_url hostname {host!r} could not be resolved")
    for _family, _type, _proto, _canonname, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(
                f"logo_url resolves to a private/reserved IP address ({ip_str}); "
                "only public IP addresses are allowed"
            )
    return v


# ---------------------------------------------------------------------------
# Company request schemas
# ---------------------------------------------------------------------------


class CreateCompanyRequest(_StrictBase):
    """Request body for POST /companies (admin)."""

    legal_name: str = Field(..., min_length=1, max_length=255)
    address: str = Field(..., min_length=1, max_length=2000)
    siret: Optional[str] = Field(None, pattern=r"^\d{14}$")
    tva_number: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{2,16}$")
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[HttpUrl] = None
    default_payment_terms: Optional[str] = Field(None, max_length=500)
    prefix_override: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,8}$")

    @field_validator("logo_url", mode="after")
    @classmethod
    def validate_logo_url(cls, v: Optional[HttpUrl]) -> Optional[HttpUrl]:
        return _validate_logo_url(v)


class UpdateCompanyRequest(_StrictBase):
    """Request body for PUT /companies/<id> (admin).

    All fields optional; extra='forbid' prevents sending id/created_by/timestamps.
    """

    legal_name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, min_length=1, max_length=2000)
    siret: Optional[str] = Field(None, pattern=r"^\d{14}$")
    tva_number: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{2,16}$")
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[HttpUrl] = None
    default_payment_terms: Optional[str] = Field(None, max_length=500)
    prefix_override: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,8}$")

    @field_validator("logo_url", mode="after")
    @classmethod
    def validate_logo_url(cls, v: Optional[HttpUrl]) -> Optional[HttpUrl]:
        return _validate_logo_url(v)


# ---------------------------------------------------------------------------
# Company membership request schemas (join code)
# ---------------------------------------------------------------------------


class JoinCompanyRequest(_StrictBase):
    """Request body for POST /companies/join (shared join code, member role)."""

    code: str = Field(..., min_length=4, max_length=32)


class AttachedUserRow(_StrictBase):
    """One row of GET /companies/<id>/attached-users.

    Access fields come from the use case; `email` / `display_name` / `phone`
    are joined from `users` so clients can render the member list directly.
    """

    user_id: UUID
    company_id: UUID
    role: str
    is_primary: bool
    attached_at: datetime
    email: Optional[str] = None
    display_name: Optional[str] = None
    phone: Optional[str] = None


class AttachedUsersListResponse(_StrictBase):
    items: list[AttachedUserRow]
    total: int


class JoinCodeResponse(_StrictBase):
    """Response of POST /companies/<id>/join-code."""

    join_code: str


# ---------------------------------------------------------------------------
# User-company request schemas
# ---------------------------------------------------------------------------


class SetPrimaryCompanyRequest(_StrictBase):
    """Request body for PUT /users/me/primary-company."""

    company_id: UUID


# ---------------------------------------------------------------------------
# Member onboarding request schemas (Phase 2 onboarding slice)
# ---------------------------------------------------------------------------


class AddMemberByPhoneRequest(_StrictBase):
    """Request body for POST /companies/<id>/members.

    ``person_id`` lets the caller resend the request after a 409
    (several un-linked candidates matched the phone) to pick one explicitly.
    """

    phone: str = Field(..., min_length=1, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    # Not pattern-restricted to member|manager here: 'admin' (and anything
    # else invalid) is rejected by the use case with a 400 business error
    # (AdminRoleNotAssignableError), not a 422 schema error.
    role: str = Field(default="member", min_length=1, max_length=20)
    person_id: Optional[UUID] = None


class UpdateMemberPayDefaultsRequest(_StrictBase):
    """Request body for PATCH /companies/<id>/members/<person_id>.

    Both fields are optional and nullable, and the two cases are NOT the same:
    an absent key leaves the stored value untouched, while an explicit ``null``
    clears it. The route reads ``model_fields_set`` to tell them apart, so the
    default here is only what an absent key deserializes to.
    """

    # Numeric(10, 2) on the column: 8 integer digits, 2 decimals. A rate of 0
    # is rejected rather than stored — "free" is not a default worth inheriting,
    # and CreateWorkerUseCase would refuse it anyway.
    default_daily_rate: Optional[Decimal] = Field(default=None, gt=0, le=Decimal("99999999.99"))
    labor_role_id: Optional[UUID] = None


class ImportMembersRequest(_StrictBase):
    """Request body for POST /companies/<id>/members/import."""

    from_company_id: UUID
    person_ids: list[UUID] = Field(..., min_length=1, max_length=200)
