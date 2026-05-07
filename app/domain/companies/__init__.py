"""Public API for the companies bounded context domain layer.

Import surface used by application and infrastructure layers.
Pure Python — no Flask, no SQLAlchemy.
"""

from app.domain.companies.company import Company
from app.domain.companies.exceptions import (
    ActiveInviteTokenAlreadyExistsError,
    CompaniesDomainError,
    CompanyAlreadyAttachedError,
    CompanyNotFoundError,
    ForbiddenCompanyError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenExpiredError,
    InviteTokenNotFoundError,
    MissingPrimaryCompanyError,
    UserCompanyAccessNotFoundError,
)
from app.domain.companies.invite_token import CompanyInviteToken
from app.domain.companies.masking import SENSITIVE_FIELDS, mask_company
from app.domain.companies.user_company_access import UserCompanyAccess

__all__ = [
    # entities
    "Company",
    "UserCompanyAccess",
    "CompanyInviteToken",
    # masking
    "mask_company",
    "SENSITIVE_FIELDS",
    # exceptions
    "CompaniesDomainError",
    "CompanyNotFoundError",
    "UserCompanyAccessNotFoundError",
    "InviteTokenNotFoundError",
    "InviteTokenExpiredError",
    "InviteTokenAlreadyRedeemedError",
    "ActiveInviteTokenAlreadyExistsError",
    "CompanyAlreadyAttachedError",
    "ForbiddenCompanyError",
    "MissingPrimaryCompanyError",
]
