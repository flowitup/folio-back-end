"""Pydantic v2 request/response schemas for the companies API.

Strict mode (extra='forbid') enforced on all request schemas.
Logo URL validation uses SSRF-safe scheme + private-IP-block logic.
"""

from __future__ import annotations

import ipaddress
import socket
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
    scheme = v.scheme
    if scheme not in ("http", "https"):
        raise ValueError("logo_url must use http or https scheme")
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
# Invite token request schemas
# ---------------------------------------------------------------------------


class RedeemInviteTokenRequest(_StrictBase):
    """Request body for POST /companies/attach-by-token."""

    token: str = Field(..., min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# User-company request schemas
# ---------------------------------------------------------------------------


class SetPrimaryCompanyRequest(_StrictBase):
    """Request body for PUT /users/me/primary-company."""

    company_id: UUID
