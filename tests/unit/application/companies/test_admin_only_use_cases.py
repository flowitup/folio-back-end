"""Unit tests verifying ForbiddenCompanyError when non-admin calls admin-only use-cases.

Required regression:
  test_role_guard_on_admin_endpoints (logic path — HTTP 403 tested in API layer)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase
from app.application.companies.create_company_usecase import CreateCompanyUseCase
from app.application.companies.delete_company_usecase import DeleteCompanyUseCase
from app.application.companies.dtos import (
    BootAttachedUserInput,
    CreateCompanyInput,
    GenerateInviteTokenInput,
    ListAllCompaniesInput,
    UpdateCompanyInput,
)
from app.application.companies.generate_invite_token_usecase import GenerateInviteTokenUseCase
from app.application.companies.list_all_companies_usecase import ListAllCompaniesUseCase
from app.application.companies.revoke_invite_token_usecase import RevokeInviteTokenUseCase
from app.application.companies.update_company_usecase import UpdateCompanyUseCase
from app.domain.companies.exceptions import ForbiddenCompanyError


def test_role_guard_on_admin_endpoints(
    company_repo, access_repo, token_repo, role_service,
    hasher, token_generator, clock, fake_session, user_id, seeded_company
):
    """test_role_guard_on_admin_endpoints — required by spec.

    Non-admin user receives ForbiddenCompanyError on every admin-only use-case.
    """
    dummy_company_id = seeded_company.id

    # CreateCompany
    create_uc = CreateCompanyUseCase(company_repo=company_repo, role_checker=role_service)
    with pytest.raises(ForbiddenCompanyError):
        create_uc.execute(
            CreateCompanyInput(caller_id=user_id, legal_name="X", address="Y"),
            fake_session,
        )

    # UpdateCompany
    update_uc = UpdateCompanyUseCase(company_repo=company_repo, role_checker=role_service)
    with pytest.raises(ForbiddenCompanyError):
        update_uc.execute(
            UpdateCompanyInput(id=dummy_company_id, caller_id=user_id, legal_name="X"),
            fake_session,
        )

    # DeleteCompany
    delete_uc = DeleteCompanyUseCase(company_repo=company_repo, role_checker=role_service)
    with pytest.raises(ForbiddenCompanyError):
        delete_uc.execute(user_id, dummy_company_id, fake_session)

    # ListAllCompanies
    list_uc = ListAllCompaniesUseCase(company_repo=company_repo, role_checker=role_service)
    with pytest.raises(ForbiddenCompanyError):
        list_uc.execute(ListAllCompaniesInput(caller_id=user_id, limit=10, offset=0))

    # GenerateInviteToken
    gen_uc = GenerateInviteTokenUseCase(
        company_repo=company_repo,
        token_repo=token_repo,
        hasher=hasher,
        token_generator=token_generator,
        clock=clock,
        role_checker=role_service,
    )
    with pytest.raises(ForbiddenCompanyError):
        gen_uc.execute(
            GenerateInviteTokenInput(company_id=dummy_company_id, caller_id=user_id),
            fake_session,
        )

    # RevokeInviteToken — uses positional args (caller_id, company_id, db_session)
    revoke_uc = RevokeInviteTokenUseCase(
        company_repo=company_repo,
        token_repo=token_repo,
        role_checker=role_service,
    )
    with pytest.raises(ForbiddenCompanyError):
        revoke_uc.execute(user_id, dummy_company_id, fake_session)

    # BootAttachedUser
    boot_uc = BootAttachedUserUseCase(
        company_repo=company_repo,
        access_repo=access_repo,
        role_checker=role_service,
    )
    with pytest.raises(ForbiddenCompanyError):
        boot_uc.execute(
            BootAttachedUserInput(
                caller_id=user_id,
                company_id=dummy_company_id,
                target_user_id=uuid4(),
            ),
            fake_session,
        )
