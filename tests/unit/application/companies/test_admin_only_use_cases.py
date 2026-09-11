"""Unit tests verifying ForbiddenCompanyError when non-admin calls admin-only use-cases.

Required regression:
  test_role_guard_on_admin_endpoints (logic path — HTTP 403 tested in API layer)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase
from app.application.companies.delete_company_usecase import DeleteCompanyUseCase
from app.application.companies.dtos import (
    BootAttachedUserInput,
    ListAllCompaniesInput,
    UpdateCompanyInput,
)
from app.application.companies.list_all_companies_usecase import ListAllCompaniesUseCase
from app.application.companies.update_company_usecase import UpdateCompanyUseCase
from app.domain.companies.exceptions import ForbiddenCompanyError


def test_role_guard_on_admin_endpoints(
    company_repo,
    access_repo,
    role_service,
    fake_session,
    user_id,
    seeded_company,
):
    """test_role_guard_on_admin_endpoints — required by spec.

    Non-admin user receives ForbiddenCompanyError on every admin-only use-case.
    """
    dummy_company_id = seeded_company.id

    # CreateCompany: Phase 2 D1/goal 1 made this self-service (any
    # authenticated user) — no longer part of this admin-only regression.
    # See tests/unit/application/companies/test_create_company.py for its
    # own (now-updated) coverage.

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
