"""M5 — every company-management use-case's `_assert_company_admin` guard is
scoped to the TARGET company, not "any company the caller admins somewhere".

Parametrized over every use-case that calls `_assert_company_admin` (or the
payment-methods package's structurally-identical inline guard): admin of
company A calling on company B must get `ForbiddenCompanyError`; admin of B
must pass the guard cleanly.

`ListPaymentMethodsUseCase` is the one deliberate exception — the use-case
raises `PaymentMethodNotFoundError` instead of `ForbiddenCompanyError` for a
non-admin, non-platform-admin caller specifically so a stranger cannot
distinguish "company exists, no access" from "company doesn't exist" (see
its module docstring). It is covered separately below with that exception.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.companies.exceptions import ForbiddenCompanyError
from tests.unit.application.companies.conftest import (
    FakeRoleService,
    InMemoryCompanyRepository,
    InMemoryUserCompanyAccessRepository,
    _FakeSession,
    make_access,
    make_company,
)
from tests.unit.application.payment_methods.conftest import (
    InMemoryPaymentMethodRepository,
    make_payment_method,
)


@pytest.fixture
def ctx():
    """Two companies, one admin each — NO platform '*:*' bypass, so only the
    per-company guard is under test."""
    admin_a_id, admin_b_id, member_b_id = uuid4(), uuid4(), uuid4()
    company_a = make_company(created_by=admin_a_id)
    company_b = make_company(created_by=admin_b_id)

    company_repo = InMemoryCompanyRepository()
    company_repo.save(company_a)
    company_repo.save(company_b)

    access_repo = InMemoryUserCompanyAccessRepository()
    access_repo.save(make_access(admin_a_id, company_a.id, role="admin"))
    access_repo.save(make_access(admin_b_id, company_b.id, role="admin"))
    access_repo.save(make_access(member_b_id, company_b.id, is_primary=False, role="member"))

    role_service = FakeRoleService()
    role_service.set_company_admin(admin_a_id, company_a.id)
    role_service.set_company_admin(admin_b_id, company_b.id)

    return SimpleNamespace(
        admin_a_id=admin_a_id,
        admin_b_id=admin_b_id,
        member_b_id=member_b_id,
        company_a=company_a,
        company_b=company_b,
        company_repo=company_repo,
        access_repo=access_repo,
        pm_repo=InMemoryPaymentMethodRepository(),
        role_service=role_service,
        session=_FakeSession(),
    )


# ---------------------------------------------------------------------------
# Scenario callables: each builds a fresh use-case and executes it against
# ctx.company_b as the given caller_id. Cross-company (caller=admin_a) must
# raise ForbiddenCompanyError; same-company (caller=admin_b) must complete
# without raising.
# ---------------------------------------------------------------------------


def _run_update_company(ctx, caller_id):
    from app.application.companies.dtos import UpdateCompanyInput
    from app.application.companies.update_company_usecase import UpdateCompanyUseCase

    usecase = UpdateCompanyUseCase(company_repo=ctx.company_repo, role_checker=ctx.role_service)
    usecase.execute(UpdateCompanyInput(id=ctx.company_b.id, caller_id=caller_id, legal_name="Renamed Co"), ctx.session)


def _run_list_attached_users(ctx, caller_id):
    from app.application.companies.dtos import ListAttachedUsersInput
    from app.application.companies.list_attached_users_usecase import ListAttachedUsersUseCase

    usecase = ListAttachedUsersUseCase(
        company_repo=ctx.company_repo, access_repo=ctx.access_repo, role_checker=ctx.role_service
    )
    usecase.execute(ListAttachedUsersInput(caller_id=caller_id, company_id=ctx.company_b.id))


def _run_set_member_role(ctx, caller_id):
    from app.application.companies.dtos import SetMemberRoleInput
    from app.application.companies.set_member_role_usecase import SetMemberRoleUseCase

    usecase = SetMemberRoleUseCase(access_repo=ctx.access_repo, role_checker=ctx.role_service)
    usecase.execute(
        SetMemberRoleInput(caller_id=caller_id, company_id=ctx.company_b.id, user_id=ctx.member_b_id, role="admin"),
        ctx.session,
    )


def _run_boot_attached_user(ctx, caller_id):
    from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase
    from app.application.companies.dtos import BootAttachedUserInput

    usecase = BootAttachedUserUseCase(
        company_repo=ctx.company_repo, access_repo=ctx.access_repo, role_checker=ctx.role_service
    )
    usecase.execute(
        BootAttachedUserInput(caller_id=caller_id, company_id=ctx.company_b.id, target_user_id=ctx.member_b_id),
        ctx.session,
    )


def _run_set_join_code(ctx, caller_id):
    from app.application.companies.join_code_usecases import SetJoinCodeUseCase
    from tests.unit.application.companies.conftest import FakeClock

    usecase = SetJoinCodeUseCase(company_repo=ctx.company_repo, clock=FakeClock(), role_checker=ctx.role_service)
    usecase.execute(ctx.company_b.id, True, ctx.session, caller_id=caller_id)


def _run_revoke_join_code(ctx, caller_id):
    from app.application.companies.join_code_usecases import SetJoinCodeUseCase
    from tests.unit.application.companies.conftest import FakeClock

    usecase = SetJoinCodeUseCase(company_repo=ctx.company_repo, clock=FakeClock(), role_checker=ctx.role_service)
    usecase.execute(ctx.company_b.id, False, ctx.session, caller_id=caller_id)


def _run_create_payment_method(ctx, caller_id):
    from app.application.payment_methods.create_payment_method_usecase import CreatePaymentMethodUseCase
    from app.application.payment_methods.dtos import CreatePaymentMethodInput

    usecase = CreatePaymentMethodUseCase(payment_method_repo=ctx.pm_repo, role_checker=ctx.role_service)
    usecase.execute(
        CreatePaymentMethodInput(
            requester_id=caller_id, company_id=ctx.company_b.id, label=f"Method {uuid4().hex[:6]}"
        ),
        ctx.session,
    )


def _run_update_payment_method(ctx, caller_id):
    from app.application.payment_methods.dtos import UpdatePaymentMethodInput
    from app.application.payment_methods.update_payment_method_usecase import UpdatePaymentMethodUseCase

    method = make_payment_method(ctx.company_b.id, label="Existing Method")
    ctx.pm_repo.save(method)
    usecase = UpdatePaymentMethodUseCase(payment_method_repo=ctx.pm_repo, role_checker=ctx.role_service)
    usecase.execute(
        UpdatePaymentMethodInput(
            requester_id=caller_id, company_id=ctx.company_b.id, payment_method_id=method.id, label="Renamed"
        ),
        ctx.session,
    )


def _run_delete_payment_method(ctx, caller_id):
    from app.application.payment_methods.delete_payment_method_usecase import DeletePaymentMethodUseCase

    method = make_payment_method(ctx.company_b.id, label="Deletable Method")
    ctx.pm_repo.save(method)
    usecase = DeletePaymentMethodUseCase(payment_method_repo=ctx.pm_repo, role_checker=ctx.role_service)
    usecase.execute(caller_id, method.id, ctx.session, company_id=ctx.company_b.id)


_SCENARIOS = [
    pytest.param(_run_update_company, id="update_company"),
    pytest.param(_run_list_attached_users, id="list_attached_users"),
    pytest.param(_run_set_member_role, id="set_member_role"),
    pytest.param(_run_boot_attached_user, id="boot_attached_user"),
    pytest.param(_run_set_join_code, id="set_join_code"),
    pytest.param(_run_revoke_join_code, id="revoke_join_code"),
    pytest.param(_run_create_payment_method, id="create_payment_method"),
    pytest.param(_run_update_payment_method, id="update_payment_method"),
    pytest.param(_run_delete_payment_method, id="delete_payment_method"),
]


@pytest.mark.parametrize("run", _SCENARIOS)
def test_admin_of_a_forbidden_on_company_b(ctx, run):
    with pytest.raises(ForbiddenCompanyError):
        run(ctx, ctx.admin_a_id)


@pytest.mark.parametrize("run", _SCENARIOS)
def test_admin_of_b_passes_the_guard(ctx, run):
    run(ctx, ctx.admin_b_id)  # must not raise


# ---------------------------------------------------------------------------
# ListPaymentMethodsUseCase — same cross-company intent, different (intentional)
# exception type. See module docstring.
# ---------------------------------------------------------------------------


def _build_list_payment_methods_usecase(ctx):
    from app.application.payment_methods.list_payment_methods_usecase import ListPaymentMethodsUseCase

    return ListPaymentMethodsUseCase(
        payment_method_repo=ctx.pm_repo,
        role_checker=ctx.role_service,
        access_repo=ctx.access_repo,
        company_repo=ctx.company_repo,
    )


def test_list_payment_methods_admin_of_a_gets_not_found_not_forbidden(ctx):
    from app.domain.payment_methods.exceptions import PaymentMethodNotFoundError

    usecase = _build_list_payment_methods_usecase(ctx)
    with pytest.raises(PaymentMethodNotFoundError):
        usecase.execute(ctx.admin_a_id, ctx.company_b.id)


def test_list_payment_methods_admin_of_b_passes(ctx):
    usecase = _build_list_payment_methods_usecase(ctx)
    result = usecase.execute(ctx.admin_b_id, ctx.company_b.id)
    assert result == []
