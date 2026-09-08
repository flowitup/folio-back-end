"""Application layer for the company_persons bounded context.

Slice A (schema/models/repos) landed the repository port + adapter. This
slice (Phase 2 onboarding, slice B) adds the admin-driven onboarding use
cases: add a member by phone, import members from another company, list the
company directory. Sign-up linking lives in
`app.application.usecases.otp_login.VerifySignupOtpUseCase`; D8 grant/deny
management is a separate slice (`manage_grants_usecase.py`).
"""

from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.company_persons.add_member_by_phone_usecase import AddMemberByPhoneUseCase
from app.application.company_persons.import_members_usecase import ImportMembersUseCase
from app.application.company_persons.list_directory_usecase import ListDirectoryUseCase
from app.application.company_persons.link_person_on_signup_usecase import LinkPersonOnSignupUseCase
from app.application.company_persons.dtos import (
    AddMemberByPhoneInput,
    AddMemberByPhoneResult,
    ImportMembersInput,
    ImportMembersResult,
    ImportedMember,
    ListDirectoryResult,
    DirectoryEntry,
)
from app.application.company_persons.exceptions import (
    CompanyPersonsError,
    AdminRoleNotAssignableError,
    MultipleCandidatesError,
    MemberAlreadyAttachedError,
    InvalidCandidatePersonError,
    SourceCompanyNotAccessibleError,
)

__all__ = [
    "CompanyPersonRepositoryPort",
    "AddMemberByPhoneUseCase",
    "ImportMembersUseCase",
    "ListDirectoryUseCase",
    "LinkPersonOnSignupUseCase",
    "AddMemberByPhoneInput",
    "AddMemberByPhoneResult",
    "ImportMembersInput",
    "ImportMembersResult",
    "ImportedMember",
    "ListDirectoryResult",
    "DirectoryEntry",
    "CompanyPersonsError",
    "AdminRoleNotAssignableError",
    "MultipleCandidatesError",
    "MemberAlreadyAttachedError",
    "InvalidCandidatePersonError",
    "SourceCompanyNotAccessibleError",
]
