"""API integration tests for company_refunded_total and company_name invoice meta fields.

Covers:
- company_refunded_total sums ONLY materials_services invoices with refundable_status='refunded'
  (refundable, refund_pending, NULL M&S, released_funds, labor must all be excluded)
- company_name present when project has a company; null when project has no company
- PATCH invoice type change rejected (400) when refundable_status is set
- PATCH type change succeeds when refundable_status is NULL
- PATCH type change succeeds after clearing refundable_status
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cr_app():
    """Flask app with two projects: one with company, one without."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class CrTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CrTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="cr_admin_role", description="CR Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="cr_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Refund Corp SARL",
            address="1 rue du Remboursement",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        # project_with_company: used to assert company_name and company_refunded_total
        project_with_company = ProjectModel(
            name="CR Project With Company",
            owner_id=admin_user.id,
            company_id=company.id,
        )
        # project_no_company: used to assert company_name=None
        project_no_company = ProjectModel(
            name="CR Project No Company",
            owner_id=admin_user.id,
        )
        db.session.add_all([project_with_company, project_no_company])
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

        test_app._test_admin_email = "cr_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_with_company_id = str(project_with_company.id)
        test_app._test_project_no_company_id = str(project_no_company.id)
        test_app._test_company_legal_name = "Refund Corp SARL"

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def cr_client(cr_app):
    return cr_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(cr_client, cr_app):
    return _login(cr_client, cr_app._test_admin_email, cr_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _list_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


def _seed_invoice(app, project_id: str, inv_type: str, refundable_status=None, items=None) -> str:
    """Insert an InvoiceModel row directly and return the string UUID."""
    from app import db

    with app.app_context():
        if items is None:
            items = [{"description": "Item", "quantity": 2, "unit_price": 100.0, "vat_rate": 0}]
        row = InvoiceModel(
            id=uuid4(),
            project_id=UUID(project_id),
            invoice_number=f"TEST-{uuid4().hex[:8]}",
            type=inv_type,
            issue_date=date.today(),
            recipient_name="Test Recipient",
            items=items,
            refundable_status=refundable_status,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


# ---------------------------------------------------------------------------
# Tests: company_refunded_total
# ---------------------------------------------------------------------------


class TestCompanyRefundedTotal:
    def test_only_refunded_ms_invoices_count(self, cr_client, cr_app, admin_token):
        """company_refunded_total counts only M&S invoices with status='refunded'.

        Mix: 1 refunded M&S (qty=2, price=100 = 200), 1 refundable M&S (excluded),
        1 refund_pending M&S (excluded), 1 null-status M&S (excluded),
        1 released_funds (excluded), 1 labor (excluded).
        Expected total = 200.00.
        """
        project_id = cr_app._test_project_with_company_id

        # Refunded M&S — should count: 2 * 100 = 200
        _seed_invoice(cr_app, project_id, "materials_services", refundable_status="refunded")
        # Other statuses and types — must NOT count
        _seed_invoice(cr_app, project_id, "materials_services", refundable_status="refundable")
        _seed_invoice(cr_app, project_id, "materials_services", refundable_status="refund_pending")
        _seed_invoice(cr_app, project_id, "materials_services", refundable_status=None)
        _seed_invoice(cr_app, project_id, "released_funds", refundable_status=None)
        _seed_invoice(cr_app, project_id, "labor", refundable_status=None)

        resp = cr_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert "company_refunded_total" in data
        # Only the one refunded M&S: 2 * 100 = 200
        assert data["company_refunded_total"] == pytest.approx(200.0, abs=0.01)

    def test_no_refunded_invoices_returns_zero(self, cr_client, cr_app, admin_token):
        """Project with no refunded invoices returns company_refunded_total=0."""
        project_id = cr_app._test_project_no_company_id

        # Add only non-refunded invoices
        _seed_invoice(cr_app, project_id, "materials_services", refundable_status="refundable")
        _seed_invoice(cr_app, project_id, "labor", refundable_status=None)

        resp = cr_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_refunded_total"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: company_name
# ---------------------------------------------------------------------------


class TestCompanyName:
    def test_company_name_present_when_project_has_company(self, cr_client, cr_app, admin_token):
        """company_name equals the company's legal_name when project has a company."""
        project_id = cr_app._test_project_with_company_id

        resp = cr_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_name"] == cr_app._test_company_legal_name

    def test_company_name_null_when_project_has_no_company(self, cr_client, cr_app, admin_token):
        """company_name is null when the project is not attached to a company."""
        project_id = cr_app._test_project_no_company_id

        resp = cr_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_name"] is None


# ---------------------------------------------------------------------------
# Tests: type-change guard
# ---------------------------------------------------------------------------


class TestInvoiceTypeChangeGuard:
    def _create_ms_invoice_with_status(self, app, project_id, refundable_status):
        """Seed a materials_services invoice with the given refundable_status; return id."""
        return _seed_invoice(app, project_id, "materials_services", refundable_status=refundable_status)

    def test_type_change_blocked_when_refundable_status_set(self, cr_client, cr_app, admin_token):
        """PATCH type change returns 400 when the invoice has a non-null refundable_status."""
        project_id = cr_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cr_app, project_id, "refundable")

        resp = cr_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "labor"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "refundable" in body.get("message", "").lower() or "type" in body.get("message", "").lower()

    def test_type_change_succeeds_when_status_null(self, cr_client, cr_app, admin_token):
        """PATCH type change succeeds when refundable_status is NULL."""
        project_id = cr_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cr_app, project_id, None)

        resp = cr_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "labor"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["type"] == "labor"

    def test_type_change_succeeds_after_clearing_status(self, cr_client, cr_app, admin_token):
        """PATCH type change succeeds after refundable_status is explicitly cleared to null."""
        from app import db

        project_id = cr_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cr_app, project_id, "refund_pending")

        # Clear status via direct DB update (simulates SetInvoiceRefundableStatusUseCase)
        with cr_app.app_context():
            row = db.session.get(InvoiceModel, UUID(invoice_id))
            row.refundable_status = None
            db.session.commit()

        resp = cr_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "others"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["type"] == "others"

    def test_same_type_patch_with_status_set_is_allowed(self, cr_client, cr_app, admin_token):
        """PATCH with the same type as existing does not trigger the guard."""
        project_id = cr_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cr_app, project_id, "refundable")

        resp = cr_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "materials_services", "recipient_name": "Updated Name"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
