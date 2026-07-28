"""API integration tests for personal_spent_total, funds_released_{company,personal}_total,
and paid_by_personal invoice meta fields.

Mirrors tests/api/test_invoice_company_spent_meta.py. Covers:
- personal_spent_total sums non-released_funds invoices paid via a personal-flagged method
- company-reimbursed rows are excluded; bank-refunded rows still count
- funds_released_company_total + funds_released_personal_total == funds_released_total
- paid_by_personal per-invoice bool reflects personal-payment method membership
- paid_by_personal present on list, GET single, POST create, and PUT update responses
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ps_app():
    """Flask app with a company-scoped project + a personal-flagged payment method."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
        SqlAlchemyPaymentMethodRepository,
    )
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class PsTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(PsTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="ps_admin_role", description="PS Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="ps_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Personal Spend Corp SARL",
            address="1 rue de la Dépense Perso",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        personal_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Carte perso",
            is_builtin=False,
            is_active=True,
            is_personal_payment=True,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        company_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Personal Spend Corp SARL",
            is_builtin=True,
            is_active=True,
            is_company_payment=True,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        regular_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Cash",
            is_builtin=True,
            is_active=True,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([personal_pm, company_pm, regular_pm])
        db.session.flush()

        project = ProjectModel(
            name="PS Project With Company",
            owner_id=admin_user.id,
            company_id=company.id,
        )
        db.session.add(project)
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _pm_repo = SqlAlchemyPaymentMethodRepository(db.session)

        _c = get_container()
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)

        test_app._test_admin_email = "ps_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_personal_pm_id = str(personal_pm.id)
        test_app._test_company_pm_id = str(company_pm.id)
        test_app._test_regular_pm_id = str(regular_pm.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def ps_client(ps_app):
    return ps_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(ps_client, ps_app):
    return _login(ps_client, ps_app._test_admin_email, ps_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _list_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


def _seed_invoice(
    app,
    project_id: str,
    inv_type: str,
    refundable_status=None,
    refunded_by=None,
    payment_method_id=None,
    items=None,
) -> str:
    """Insert an InvoiceModel row directly and return the string UUID."""
    from app import db

    with app.app_context():
        if items is None:
            items = [{"description": "Item", "quantity": 1, "unit_price": 100.0, "vat_rate": 0}]
        row = InvoiceModel(
            id=uuid4(),
            project_id=UUID(project_id),
            invoice_number=f"TEST-{uuid4().hex[:8]}",
            type=inv_type,
            issue_date=date.today(),
            recipient_name="Test Recipient",
            items=items,
            refundable_status=refundable_status,
            refunded_by=refunded_by,
            payment_method_id=UUID(payment_method_id) if payment_method_id else None,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


# ---------------------------------------------------------------------------
# Tests: personal_spent_total
# ---------------------------------------------------------------------------


class TestPersonalSpentTotal:
    def test_personal_paid_invoice_counts(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        _seed_invoice(
            ps_app,
            project_id,
            "materials_services",
            payment_method_id=personal_pm_id,
            items=[{"description": "Perso", "quantity": 1, "unit_price": 321.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert "personal_spent_total" in data
        assert data["personal_spent_total"] >= 321.0

    def test_company_reimbursed_personal_expense_excluded(self, ps_client, ps_app, admin_token):
        """A personal expense the company reimbursed (refunded_by != 'bank') is
        excluded from personal_spent_total — it's company spend now."""
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        inv_id = _seed_invoice(
            ps_app,
            project_id,
            "materials_services",
            refundable_status="refunded",
            refunded_by="company",
            payment_method_id=personal_pm_id,
            items=[{"description": "Reimbursed", "quantity": 1, "unit_price": 5000.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        # The 5000 sentinel must never leak into personal_spent_total.
        assert data["personal_spent_total"] < 5000.0
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_personal"] is True

    def test_bank_refunded_personal_expense_still_counts(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        # Capture the total BEFORE seeding so the assertion is an exact delta, not a
        # monotonic threshold — the fixture's project accumulates invoices across tests
        # (module-scoped, no per-test cleanup), so ">= 77.0" would pass even if
        # bank-refunded rows were wrongly excluded from personal spend.
        before_resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        before_total = before_resp.get_json()["personal_spent_total"]

        _seed_invoice(
            ps_app,
            project_id,
            "materials_services",
            refundable_status="refunded",
            refunded_by="bank",
            payment_method_id=personal_pm_id,
            items=[{"description": "BankRefunded", "quantity": 1, "unit_price": 77.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        assert data["personal_spent_total"] == pytest.approx(before_total + 77.0, abs=0.01)

    def test_released_funds_never_counted(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        _seed_invoice(
            ps_app,
            project_id,
            "released_funds",
            payment_method_id=personal_pm_id,
            items=[{"description": "Released", "quantity": 1, "unit_price": 20000.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        assert data["personal_spent_total"] < 20000.0


# ---------------------------------------------------------------------------
# Tests: funds_released_company_total / funds_released_personal_total
# ---------------------------------------------------------------------------


class TestFundsReleasedSplit:
    def test_split_totals_sum_to_funds_released_total(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        _seed_invoice(
            ps_app,
            project_id,
            "released_funds",
            payment_method_id=personal_pm_id,
            items=[{"description": "Personal release", "quantity": 1, "unit_price": 42.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()

        assert "funds_released_company_total" in data
        assert "funds_released_personal_total" in data
        total = data["funds_released_company_total"] + data["funds_released_personal_total"]
        assert total == pytest.approx(data["funds_released_total"], abs=0.01)
        assert data["funds_released_personal_total"] >= 42.0

    def test_unflagged_method_release_counts_as_company(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        regular_pm_id = ps_app._test_regular_pm_id

        _seed_invoice(
            ps_app,
            project_id,
            "released_funds",
            payment_method_id=regular_pm_id,
            items=[{"description": "Regular release", "quantity": 1, "unit_price": 88.0, "vat_rate": 0}],
        )

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        assert data["funds_released_company_total"] >= 88.0


# ---------------------------------------------------------------------------
# Tests: paid_by_personal per-invoice flag across all response shapes
# ---------------------------------------------------------------------------


class TestPaidByPersonalPerInvoice:
    def test_paid_by_personal_true_for_personal_payment_method(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        inv_id = _seed_invoice(ps_app, project_id, "labor", payment_method_id=personal_pm_id)

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_personal"] is True
        assert inv["paid_by_company"] is False

    def test_paid_by_personal_false_for_company_payment_method(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        company_pm_id = ps_app._test_company_pm_id

        inv_id = _seed_invoice(ps_app, project_id, "labor", payment_method_id=company_pm_id)

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_personal"] is False
        assert inv["paid_by_company"] is True

    def test_paid_by_personal_false_when_no_payment_method(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id

        inv_id = _seed_invoice(ps_app, project_id, "labor", payment_method_id=None)

        resp = ps_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_personal"] is False

    def test_get_single_includes_paid_by_personal(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        inv_id = _seed_invoice(ps_app, project_id, "labor", payment_method_id=personal_pm_id)

        resp = ps_client.get(_invoice_url(project_id, inv_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "paid_by_personal" in body
        assert body["paid_by_personal"] is True

    def test_put_response_includes_paid_by_personal(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        inv_id = _seed_invoice(ps_app, project_id, "labor", payment_method_id=personal_pm_id)

        resp = ps_client.put(
            _invoice_url(project_id, inv_id),
            json={"recipient_name": "Updated Recipient"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "paid_by_personal" in body
        assert body["paid_by_personal"] is True

    def test_post_create_response_includes_paid_by_personal(self, ps_client, ps_app, admin_token):
        project_id = ps_app._test_project_id
        personal_pm_id = ps_app._test_personal_pm_id

        resp = ps_client.post(
            f"/api/v1/projects/{project_id}/invoices",
            headers=_auth(admin_token),
            json={
                "type": "materials_services",
                "issue_date": "2026-06-14",
                "recipient_name": "ACME",
                "payment_method_id": personal_pm_id,
                "items": [{"description": "Cement", "quantity": 1, "unit_price": 60, "vat_rate": 0}],
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "paid_by_personal" in body
        assert body["paid_by_personal"] is True
        assert "paid_by_company" in body
        assert body["paid_by_company"] is False
