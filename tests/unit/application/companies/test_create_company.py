"""Unit tests for CreateCompanyUseCase."""

from __future__ import annotations


from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.application.companies.create_company_usecase import CreateCompanyUseCase
from app.application.companies.dtos import CreateCompanyInput
from app.domain.companies.user_company_access import UserCompanyAccess


@pytest.fixture
def usecase(company_repo, access_repo):
    return CreateCompanyUseCase(company_repo=company_repo, access_repo=access_repo)


def _inp(caller_id, **overrides):
    defaults = dict(
        caller_id=caller_id,
        legal_name="Test Corp SAS",
        address="1 rue de la Paix, 75001 Paris",
    )
    defaults.update(overrides)
    return CreateCompanyInput(**defaults)


class TestCreateCompanyHappyPath:
    def test_admin_creates_company(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        assert result.legal_name == "Test Corp SAS"
        assert result.address == "1 rue de la Paix, 75001 Paris"

    def test_company_persisted(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        stored = company_repo.find_by_id(result.id)
        assert stored is not None
        assert stored.legal_name == result.legal_name

    def test_optional_fields_passed_through(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(
            _inp(
                admin_id,
                siret="12345678901234",
                tva_number="FR12345678901",
                iban="FR76300",
                bic="BNPAFRPP",
                prefix_override="TST",
            ),
            fake_session,
        )
        assert result.siret == "12345678901234"
        assert result.prefix_override == "TST"

    def test_created_by_set_to_caller(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        stored = company_repo.find_by_id(result.id)
        assert stored.created_by == admin_id

    def test_own_company_is_primary_after_being_attached_to_another(self, usecase, access_repo, user_id, fake_session):
        """An account attached by someone else (never primary) still gets its own first company as primary."""
        access_repo.save(
            UserCompanyAccess(
                user_id=user_id, company_id=uuid4(), is_primary=False, attached_at=datetime.now(timezone.utc)
            )
        )
        result = usecase.execute(_inp(user_id), fake_session)
        own = access_repo.find(user_id, result.id)
        assert own.is_primary is True


class TestCreateCompanyGuards:
    def test_non_admin_creates_company_self_service(self, usecase, user_id, fake_session):
        """Phase 2 D1/goal 1: company creation is self-service — a caller with
        no platform `*:*` permission succeeds (no ForbiddenCompanyError)."""
        result = usecase.execute(_inp(user_id), fake_session)
        assert result.legal_name == "Test Corp SAS"

    def test_blank_legal_name_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, legal_name="   "), fake_session)

    def test_blank_address_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, address=""), fake_session)

    def test_invalid_prefix_override_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, prefix_override="invalid!"), fake_session)
