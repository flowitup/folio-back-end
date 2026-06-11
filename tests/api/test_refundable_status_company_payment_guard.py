"""API integration tests: refundable_status guard for company-payment invoices.

When an invoice was paid directly by the company (payment_method.is_company_payment=True),
setting a non-null refundable_status returns 400 with the appropriate message.
Clearing (null) stays allowed.  Non-company-payment invoices are unaffected.
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
def rg_app():
    """Flask app wired for refundable-status company-payment guard tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class RgTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(RgTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="rg_admin_role", description="RG Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="rg_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Guard Corp SARL",
            address="1 rue de la Garde",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        # company-payment method
        company_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Guard Corp SARL",
            is_builtin=True,
            is_active=True,
            is_company_payment=True,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        # regular (non-company) method
        regular_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Cash",
            is_builtin=True,
            is_active=True,
            is_company_payment=False,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_pm, regular_pm])
        db.session.flush()

        project = ProjectModel(
            name="RG Project",
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

        _c = get_container()
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)
        # set_refundable_status_usecase is wired by configure_container when
        # invoice_repository is provided; access_repo=None means the superadmin
        # JWT claim is used for authorization (which our admin user satisfies).

        test_app._test_admin_email = "rg_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_company_pm_id = str(company_pm.id)
        test_app._test_regular_pm_id = str(regular_pm.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def rg_client(rg_app):
    return rg_app.test_client()


@pytest.fixture
def admin_token(rg_client, rg_app):
    resp = rg_client.post(
        "/api/v1/auth/login",
        json={"email": rg_app._test_admin_email, "password": rg_app._test_admin_password},
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_invoice(app, project_id: str, payment_method_id=None, refundable_status=None) -> str:
    """Insert a materials_services InvoiceModel row directly; return string UUID."""
    from app import db

    with app.app_context():
        row = InvoiceModel(
            id=uuid4(),
            project_id=UUID(project_id),
            invoice_number=f"RG-{uuid4().hex[:8]}",
            type="materials_services",
            issue_date=date.today(),
            recipient_name="Recipient",
            items=[{"description": "Line", "quantity": 1, "unit_price": 100.0, "vat_rate": 0}],
            payment_method_id=UUID(payment_method_id) if payment_method_id else None,
            refundable_status=refundable_status,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


def _set_status_url(invoice_id):
    return f"/api/v1/billing/materials-expenses/{invoice_id}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefundableStatusCompanyPaymentGuard:
    def test_set_status_on_company_paid_invoice_returns_400(self, rg_client, rg_app, admin_token):
        """Setting a non-null refundable_status on a company-payment invoice returns 400."""
        invoice_id = _seed_invoice(
            rg_app,
            rg_app._test_project_id,
            payment_method_id=rg_app._test_company_pm_id,
        )

        resp = rg_client.patch(
            _set_status_url(invoice_id),
            json={"refundable_status": "refundable"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "company" in body.get("message", "").lower()

    def test_clearing_status_on_company_paid_invoice_allowed(self, rg_client, rg_app, admin_token):
        """Clearing refundable_status (null) on a company-payment invoice is allowed."""
        invoice_id = _seed_invoice(
            rg_app,
            rg_app._test_project_id,
            payment_method_id=rg_app._test_company_pm_id,
            refundable_status="refundable",
        )

        resp = rg_client.patch(
            _set_status_url(invoice_id),
            json={"refundable_status": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] is None

    def test_set_status_on_regular_payment_invoice_unaffected(self, rg_client, rg_app, admin_token):
        """Setting refundable_status on an invoice with a non-company-payment method works."""
        invoice_id = _seed_invoice(
            rg_app,
            rg_app._test_project_id,
            payment_method_id=rg_app._test_regular_pm_id,
        )

        resp = rg_client.patch(
            _set_status_url(invoice_id),
            json={"refundable_status": "refundable"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] == "refundable"

    def test_set_status_on_invoice_without_payment_method_unaffected(self, rg_client, rg_app, admin_token):
        """Setting refundable_status on an invoice with no payment method works."""
        invoice_id = _seed_invoice(rg_app, rg_app._test_project_id, payment_method_id=None)

        resp = rg_client.patch(
            _set_status_url(invoice_id),
            json={"refundable_status": "refund_pending"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] == "refund_pending"

    def test_guard_message_mentions_company(self, rg_client, rg_app, admin_token):
        """400 error message clearly states the expense was paid by the company."""
        invoice_id = _seed_invoice(
            rg_app,
            rg_app._test_project_id,
            payment_method_id=rg_app._test_company_pm_id,
        )

        resp = rg_client.patch(
            _set_status_url(invoice_id),
            json={"refundable_status": "refund_pending"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        message = resp.get_json().get("message", "")
        assert "company" in message.lower()
