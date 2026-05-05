"""Serialization helpers for companies infrastructure layer.

Converts between domain entities (Company, UserCompanyAccess, CompanyInviteToken)
and their ORM model counterparts. Kept in one module so all three repositories
share a single source of truth for field mapping.
"""

from __future__ import annotations

from app.domain.companies.company import Company
from app.domain.companies.invite_token import CompanyInviteToken
from app.domain.companies.user_company_access import UserCompanyAccess
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_invite_token import CompanyInviteTokenModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


def deserialize_company_orm(row: CompanyModel) -> Company:
    """Map CompanyModel → Company domain entity."""
    return Company(
        id=row.id,
        legal_name=row.legal_name,
        address=row.address,
        siret=row.siret,
        tva_number=row.tva_number,
        iban=row.iban,
        bic=row.bic,
        logo_url=row.logo_url,
        default_payment_terms=row.default_payment_terms,
        prefix_override=row.prefix_override,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def serialize_company_to_orm(company: Company, row: CompanyModel) -> None:
    """Write all mutable Company fields onto an existing ORM row (in-place)."""
    row.id = company.id
    row.legal_name = company.legal_name
    row.address = company.address
    row.siret = company.siret
    row.tva_number = company.tva_number
    row.iban = company.iban
    row.bic = company.bic
    row.logo_url = company.logo_url
    row.default_payment_terms = company.default_payment_terms
    row.prefix_override = company.prefix_override
    row.created_by = company.created_by
    row.created_at = company.created_at
    row.updated_at = company.updated_at


# ---------------------------------------------------------------------------
# UserCompanyAccess
# ---------------------------------------------------------------------------


def deserialize_access_orm(row: UserCompanyAccessModel) -> UserCompanyAccess:
    """Map UserCompanyAccessModel → UserCompanyAccess domain entity."""
    return UserCompanyAccess(
        user_id=row.user_id,
        company_id=row.company_id,
        is_primary=row.is_primary,
        attached_at=row.attached_at,
    )


def serialize_access_to_orm(access: UserCompanyAccess, row: UserCompanyAccessModel) -> None:
    """Write all mutable UserCompanyAccess fields onto an existing ORM row (in-place)."""
    row.user_id = access.user_id
    row.company_id = access.company_id
    row.is_primary = access.is_primary
    row.attached_at = access.attached_at


# ---------------------------------------------------------------------------
# CompanyInviteToken
# ---------------------------------------------------------------------------


def deserialize_token_orm(row: CompanyInviteTokenModel) -> CompanyInviteToken:
    """Map CompanyInviteTokenModel → CompanyInviteToken domain entity."""
    return CompanyInviteToken(
        id=row.id,
        company_id=row.company_id,
        token_hash=row.token_hash,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        redeemed_at=row.redeemed_at,
        redeemed_by=row.redeemed_by,
    )


def serialize_token_to_orm(token: CompanyInviteToken, row: CompanyInviteTokenModel) -> None:
    """Write all mutable CompanyInviteToken fields onto an existing ORM row (in-place)."""
    row.id = token.id
    row.company_id = token.company_id
    row.token_hash = token.token_hash
    row.created_by = token.created_by
    row.created_at = token.created_at
    row.expires_at = token.expires_at
    row.redeemed_at = token.redeemed_at
    row.redeemed_by = token.redeemed_by
