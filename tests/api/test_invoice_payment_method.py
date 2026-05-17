"""API integration tests for invoice + payment_method interactions.

Covers:
- create invoice with valid payment_method_id → both columns set, label snapshotted
- create invoice with method from different company → 403
- create invoice with inactive method → 409
- update invoice with payment_method_id=null → both columns cleared
- update invoice with new payment_method_id → label snapshot updated
- list invoices includes payment_method_id + payment_method_label
- get invoice after method renamed → shows OLD snapshot label (audit safety)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inv_pm_app():
    """Flask app wired with invoice + payment_method use-cases for snapshot tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
        SqlAlchemyPaymentMethodRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.application.payment_methods.list_payment_methods_usecase import ListPaymentMethodsUseCase
    from app.application.payment_methods.create_payment_method_usecase import CreatePaymentMethodUseCase
    from app.application.payment_methods.update_payment_method_usecase import UpdatePaymentMethodUseCase
    from app.application.payment_methods.delete_payment_method_usecase import DeletePaymentMethodUseCase
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class InvPmTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InvPmTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        # Permissions — name must be "*:*" so AuthorizationService.has_permission("*:*") works.
        # "*:*" grants all access including project:create (checked by can_mutate_project),
        # project:read and project:manage_invoices checked from JWT permissions list.
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="inv_pm_admin", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="inv_pm_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        # Company A (the "home" company)
        company_a = CompanyModel(
            id=uuid4(),
            legal_name="Company A SARL",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        # Company B (cross-company attack company)
        company_b = CompanyModel(
            id=uuid4(),
            legal_name="Company B SAS",
            address="2 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_a, company_b])
        db.session.flush()

        project = ProjectModel(
            name="Inv PM Test Project",
            owner_id=admin_user.id,
            company_id=company_a.id,
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
        _pm_repo = SqlAlchemyPaymentMethodRepository(db.session)
        _company_repo = SqlAlchemyCompanyRepository(db.session)
        _access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        _role_checker = _c.authorization_service

        _c.payment_method_repo = _pm_repo
        _c.company_repo = _company_repo
        _c.user_company_access_repo = _access_repo

        _c.list_payment_methods_usecase = ListPaymentMethodsUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
            access_repo=_access_repo,
            company_repo=_company_repo,
        )
        _c.create_payment_method_usecase = CreatePaymentMethodUseCase(
            payment_method_repo=_pm_repo, role_checker=_role_checker
        )
        _c.update_payment_method_usecase = UpdatePaymentMethodUseCase(
            payment_method_repo=_pm_repo, role_checker=_role_checker
        )
        _c.delete_payment_method_usecase = DeletePaymentMethodUseCase(
            payment_method_repo=_pm_repo, role_checker=_role_checker
        )

        # Re-wire invoice use-cases with payment_method_repo injected
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)

        test_app._test_admin_email = "inv_pm_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_company_a_id = str(company_a.id)
        test_app._test_company_b_id = str(company_b.id)
        test_app._test_project_id = str(project.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_pm_client(inv_pm_app):
    return inv_pm_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_pm_client, inv_pm_app):
    return _login(inv_pm_client, inv_pm_app._test_admin_email, inv_pm_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_pm_row(app, company_id: str, label="Wire Transfer", is_builtin=False, is_active=True):
    """Insert a PaymentMethodModel directly and return its string UUID."""
    from app import db

    with app.app_context():
        now = datetime.now(timezone.utc)
        row = PaymentMethodModel(
            id=uuid4(),
            company_id=UUID(company_id),
            label=label,
            is_builtin=is_builtin,
            is_active=is_active,
            created_by=UUID(app._test_admin_user_id),
            created_at=now,
            updated_at=now,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


def _base_invoice_body():
    return {
        "type": "released_funds",
        "issue_date": date.today().isoformat(),
        "recipient_name": "ACME Corp",
        "items": [{"description": "Work", "quantity": 1, "unit_price": 100}],
    }


def _create_invoice_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateInvoiceWithPaymentMethod:
    def test_valid_payment_method_id_sets_both_columns(self, inv_pm_client, inv_pm_app, admin_token):
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Wire A")

        body = {**_base_invoice_body(), "payment_method_id": pm_id}
        resp = inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["payment_method_id"] == pm_id
        assert data["payment_method_label"] == "Wire A"

    def test_method_belonging_to_different_company_returns_403(self, inv_pm_client, inv_pm_app, admin_token):
        pm_id_b = _make_pm_row(inv_pm_app, inv_pm_app._test_company_b_id, label="Wire B")

        body = {**_base_invoice_body(), "payment_method_id": pm_id_b}
        resp = inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403

    def test_inactive_method_returns_409(self, inv_pm_client, inv_pm_app, admin_token):
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Inactive C", is_active=False)

        body = {**_base_invoice_body(), "payment_method_id": pm_id}
        resp = inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409

    def test_no_payment_method_both_columns_null(self, inv_pm_client, inv_pm_app, admin_token):
        resp = inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=_base_invoice_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["payment_method_id"] is None
        assert data["payment_method_label"] is None


class TestUpdateInvoicePaymentMethod:
    def _create_invoice(self, client, app, token, pm_id=None):
        body = _base_invoice_body()
        if pm_id:
            body["payment_method_id"] = pm_id
        resp = client.post(
            _create_invoice_url(app._test_project_id),
            json=body,
            headers=_auth(token),
        )
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_update_clears_payment_method(self, inv_pm_client, inv_pm_app, admin_token):
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Wire D")
        invoice_id = self._create_invoice(inv_pm_client, inv_pm_app, admin_token, pm_id)

        resp = inv_pm_client.put(
            _invoice_url(inv_pm_app._test_project_id, invoice_id),
            json={"payment_method_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["payment_method_id"] is None
        assert data["payment_method_label"] is None

    def test_update_with_new_payment_method_updates_snapshot(self, inv_pm_client, inv_pm_app, admin_token):
        pm1_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Old Method E")
        pm2_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="New Method E")
        invoice_id = self._create_invoice(inv_pm_client, inv_pm_app, admin_token, pm1_id)

        resp = inv_pm_client.put(
            _invoice_url(inv_pm_app._test_project_id, invoice_id),
            json={"payment_method_id": pm2_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["payment_method_id"] == pm2_id
        assert data["payment_method_label"] == "New Method E"

    def test_omitting_payment_method_id_field_leaves_it_unchanged(self, inv_pm_client, inv_pm_app, admin_token):
        """PATCH without payment_method_id key must not touch existing value."""
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Wire F")
        invoice_id = self._create_invoice(inv_pm_client, inv_pm_app, admin_token, pm_id)

        resp = inv_pm_client.put(
            _invoice_url(inv_pm_app._test_project_id, invoice_id),
            json={"recipient_name": "Updated Corp"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["payment_method_id"] == pm_id
        assert data["payment_method_label"] == "Wire F"


class TestInvoiceListGetIncludesPaymentMethodFields:
    def test_list_invoices_includes_payment_method_fields(self, inv_pm_client, inv_pm_app, admin_token):
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Wire G")
        body = {**_base_invoice_body(), "payment_method_id": pm_id}
        inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )

        resp = inv_pm_client.get(
            _create_invoice_url(inv_pm_app._test_project_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        invoices = resp.get_json()["invoices"]
        # At least one invoice with payment method
        pm_invoices = [i for i in invoices if i.get("payment_method_id") == pm_id]
        assert len(pm_invoices) >= 1
        assert pm_invoices[0]["payment_method_label"] == "Wire G"


class TestSnapshotAuditSafety:
    def test_get_invoice_after_method_renamed_shows_old_label(self, inv_pm_client, inv_pm_app, admin_token):
        """Snapshot audit safety: renaming the payment method must NOT change
        the label stored on existing invoices."""
        pm_id = _make_pm_row(inv_pm_app, inv_pm_app._test_company_a_id, label="Original Label H")

        # Create invoice — snapshot "Original Label H"
        body = {**_base_invoice_body(), "payment_method_id": pm_id}
        resp = inv_pm_client.post(
            _create_invoice_url(inv_pm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        invoice_id = resp.get_json()["id"]

        # Rename the payment method
        resp_patch = inv_pm_client.patch(
            f"/api/v1/companies/{inv_pm_app._test_company_a_id}/payment-methods/{pm_id}",
            json={"label": "Renamed Label H"},
            headers=_auth(admin_token),
        )
        assert resp_patch.status_code == 200

        # Get the original invoice — snapshot must still show old label
        resp_get = inv_pm_client.get(
            _invoice_url(inv_pm_app._test_project_id, invoice_id),
            headers=_auth(admin_token),
        )
        assert resp_get.status_code == 200
        data = resp_get.get_json()
        assert data["payment_method_label"] == "Original Label H"
        assert data["payment_method_id"] == pm_id
