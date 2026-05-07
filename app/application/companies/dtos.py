"""DTOs (frozen dataclasses) for the companies application layer.

Input DTOs: carry caller-supplied data into use-cases.
Response DTOs: carry serialisation-friendly data out of use-cases.

No Pydantic here — Pydantic is the API boundary concern (phase 04).
Sensitive fields (siret, tva_number, iban, bic) are masked by use-cases
before populating CompanyResponse when the caller is not an admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.domain.companies.company import Company
from app.domain.companies.invite_token import CompanyInviteToken
from app.domain.companies.user_company_access import UserCompanyAccess


# ---------------------------------------------------------------------------
# Company input DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateCompanyInput:
    """Input for CreateCompanyUseCase.

    caller_id is the admin user creating the company.
    All other fields map to Company entity fields.
    """

    caller_id: UUID
    legal_name: str
    address: str
    siret: Optional[str] = None
    tva_number: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[str] = None
    default_payment_terms: Optional[str] = None
    prefix_override: Optional[str] = None


@dataclass(frozen=True)
class UpdateCompanyInput:
    """Input for UpdateCompanyUseCase — all fields optional except id."""

    id: UUID
    caller_id: UUID
    legal_name: Optional[str] = None
    address: Optional[str] = None
    siret: Optional[str] = None
    tva_number: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[str] = None
    default_payment_terms: Optional[str] = None
    prefix_override: Optional[str] = None


# ---------------------------------------------------------------------------
# Invite token DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerateInviteTokenInput:
    """Input for GenerateInviteTokenUseCase."""

    company_id: UUID
    caller_id: UUID
    regenerate: bool = False  # True → revoke existing active token first


@dataclass(frozen=True)
class GenerateInviteTokenOutput:
    """Result of GenerateInviteTokenUseCase.

    plaintext_token is returned ONCE to the caller (copy-to-clipboard).
    Only the argon2 hash is persisted. Do not log or cache plaintext_token.
    """

    plaintext_token: str
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class RevokeInviteTokenInput:
    """Input for RevokeInviteTokenUseCase."""

    company_id: UUID
    caller_id: UUID


@dataclass(frozen=True)
class RedeemInviteTokenInput:
    """Input for RedeemInviteTokenUseCase."""

    user_id: UUID
    plaintext_token: str


# ---------------------------------------------------------------------------
# User-company access DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetPrimaryCompanyInput:
    """Input for SetPrimaryCompanyUseCase."""

    user_id: UUID
    company_id: UUID


@dataclass(frozen=True)
class DetachCompanyInput:
    """Input for DetachCompanyUseCase."""

    user_id: UUID
    company_id: UUID


@dataclass(frozen=True)
class BootAttachedUserInput:
    """Input for BootAttachedUserUseCase (admin removes a user from a company)."""

    caller_id: UUID
    company_id: UUID
    target_user_id: UUID


# ---------------------------------------------------------------------------
# List / get inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListAllCompaniesInput:
    """Input for ListAllCompaniesUseCase (admin paginated list)."""

    caller_id: UUID
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class GetCompanyInput:
    """Input for GetCompanyUseCase."""

    caller_id: UUID
    company_id: UUID
    is_admin: bool = False  # pre-resolved by API layer from caller's token


@dataclass(frozen=True)
class ListAttachedUsersInput:
    """Input for ListAttachedUsersUseCase (admin view of a company's users).

    limit/offset: H5 pagination (default 50, max 200 enforced at API layer).
    """

    caller_id: UUID
    company_id: UUID
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class ListAttachedUsersResult:
    """Paginated result for ListAttachedUsersUseCase."""

    items: list  # list[UserCompanyAccessResponse] — forward-ref avoided
    total: int


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyResponse:
    """Serialisable company.

    Sensitive fields (siret, tva_number, iban, bic) may be masked when the
    caller is not an admin. Use-cases apply mask_company() before calling
    CompanyResponse.from_entity().
    """

    id: UUID
    legal_name: str
    address: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    siret: Optional[str] = None
    tva_number: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[str] = None
    default_payment_terms: Optional[str] = None
    prefix_override: Optional[str] = None

    @staticmethod
    def from_entity(company: Company) -> "CompanyResponse":
        """Build response DTO from a (possibly already masked) domain entity."""
        return CompanyResponse(
            id=company.id,
            legal_name=company.legal_name,
            address=company.address,
            created_by=company.created_by,
            created_at=company.created_at,
            updated_at=company.updated_at,
            siret=company.siret,
            tva_number=company.tva_number,
            iban=company.iban,
            bic=company.bic,
            logo_url=company.logo_url,
            default_payment_terms=company.default_payment_terms,
            prefix_override=company.prefix_override,
        )


@dataclass(frozen=True)
class UserCompanyAccessResponse:
    """Serialisable user-company access row."""

    user_id: UUID
    company_id: UUID
    is_primary: bool
    attached_at: datetime

    @staticmethod
    def from_entity(access: UserCompanyAccess) -> "UserCompanyAccessResponse":
        """Build response DTO from a domain entity."""
        return UserCompanyAccessResponse(
            user_id=access.user_id,
            company_id=access.company_id,
            is_primary=access.is_primary,
            attached_at=access.attached_at,
        )


@dataclass(frozen=True)
class MyCompanyResponse:
    """Combined Company + UserCompanyAccess for a user's own company list."""

    company: CompanyResponse
    access: UserCompanyAccessResponse


@dataclass(frozen=True)
class ListMyCompaniesResult:
    """Result of ListMyCompaniesUseCase."""

    items: list[MyCompanyResponse]


@dataclass(frozen=True)
class ListAllCompaniesResult:
    """Result of ListAllCompaniesUseCase (admin)."""

    items: list[CompanyResponse]
    total: int


@dataclass(frozen=True)
class InviteTokenResponse:
    """Serialisable invite token (no plaintext, no hash — metadata only)."""

    id: UUID
    company_id: UUID
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    redeemed_at: Optional[datetime] = None
    redeemed_by: Optional[UUID] = None

    @staticmethod
    def from_entity(token: CompanyInviteToken) -> "InviteTokenResponse":
        """Build response DTO from a domain entity."""
        return InviteTokenResponse(
            id=token.id,
            company_id=token.company_id,
            created_by=token.created_by,
            created_at=token.created_at,
            expires_at=token.expires_at,
            redeemed_at=token.redeemed_at,
            redeemed_by=token.redeemed_by,
        )
