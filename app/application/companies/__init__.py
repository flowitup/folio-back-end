"""Public API for the companies application layer.

Re-exports all use-case classes, key DTO types, and ports for import by
infrastructure (wiring) and API (blueprints) layers.

No Flask / SQLAlchemy / infrastructure imports here.
"""

# --- Use-cases: admin ---
from app.application.companies.create_company_usecase import CreateCompanyUseCase
from app.application.companies.update_company_usecase import UpdateCompanyUseCase
from app.application.companies.delete_company_usecase import DeleteCompanyUseCase
from app.application.companies.list_all_companies_usecase import ListAllCompaniesUseCase
from app.application.companies.generate_invite_token_usecase import GenerateInviteTokenUseCase
from app.application.companies.revoke_invite_token_usecase import RevokeInviteTokenUseCase
from app.application.companies.list_attached_users_usecase import ListAttachedUsersUseCase
from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase

# --- Use-cases: authenticated user ---
from app.application.companies.list_my_companies_usecase import ListMyCompaniesUseCase
from app.application.companies.get_company_usecase import GetCompanyUseCase
from app.application.companies.redeem_invite_token_usecase import RedeemInviteTokenUseCase
from app.application.companies.set_primary_company_usecase import SetPrimaryCompanyUseCase
from app.application.companies.detach_company_usecase import DetachCompanyUseCase

# --- DTOs ---
from app.application.companies.dtos import (
    CreateCompanyInput,
    UpdateCompanyInput,
    GenerateInviteTokenInput,
    GenerateInviteTokenOutput,
    RevokeInviteTokenInput,
    RedeemInviteTokenInput,
    SetPrimaryCompanyInput,
    DetachCompanyInput,
    BootAttachedUserInput,
    ListAllCompaniesInput,
    GetCompanyInput,
    ListAttachedUsersInput,
    ListAttachedUsersResult,
    CompanyResponse,
    UserCompanyAccessResponse,
    MyCompanyResponse,
    ListMyCompaniesResult,
    ListAllCompaniesResult,
    InviteTokenResponse,
)

# --- Ports ---
from app.application.companies.ports import (
    CompanyRepositoryPort,
    UserCompanyAccessRepositoryPort,
    CompanyInviteTokenRepositoryPort,
    Argon2HasherPort,
    SecureTokenGeneratorPort,
    ClockPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)

# --- Domain exceptions re-exported for convenience ---
from app.domain.companies.exceptions import (
    CompaniesDomainError,
    CompanyNotFoundError,
    UserCompanyAccessNotFoundError,
    InviteTokenNotFoundError,
    InviteTokenExpiredError,
    InviteTokenAlreadyRedeemedError,
    ActiveInviteTokenAlreadyExistsError,
    CompanyAlreadyAttachedError,
    ForbiddenCompanyError,
    MissingPrimaryCompanyError,
    InviteTokenSystemOverloadError,
)

__all__ = [
    # use-cases: admin
    "CreateCompanyUseCase",
    "UpdateCompanyUseCase",
    "DeleteCompanyUseCase",
    "ListAllCompaniesUseCase",
    "GenerateInviteTokenUseCase",
    "RevokeInviteTokenUseCase",
    "ListAttachedUsersUseCase",
    "BootAttachedUserUseCase",
    # use-cases: authenticated user
    "ListMyCompaniesUseCase",
    "GetCompanyUseCase",
    "RedeemInviteTokenUseCase",
    "SetPrimaryCompanyUseCase",
    "DetachCompanyUseCase",
    # DTOs
    "CreateCompanyInput",
    "UpdateCompanyInput",
    "GenerateInviteTokenInput",
    "GenerateInviteTokenOutput",
    "RevokeInviteTokenInput",
    "RedeemInviteTokenInput",
    "SetPrimaryCompanyInput",
    "DetachCompanyInput",
    "BootAttachedUserInput",
    "ListAllCompaniesInput",
    "GetCompanyInput",
    "ListAttachedUsersInput",
    "ListAttachedUsersResult",
    "CompanyResponse",
    "UserCompanyAccessResponse",
    "MyCompanyResponse",
    "ListMyCompaniesResult",
    "ListAllCompaniesResult",
    "InviteTokenResponse",
    # ports
    "CompanyRepositoryPort",
    "UserCompanyAccessRepositoryPort",
    "CompanyInviteTokenRepositoryPort",
    "Argon2HasherPort",
    "SecureTokenGeneratorPort",
    "ClockPort",
    "RoleCheckerPort",
    "TransactionalSessionPort",
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
    "InviteTokenSystemOverloadError",
]
