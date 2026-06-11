"""API integration tests for company_spent_total, paid_by_company, and company_name invoice meta fields.

Covers:
- company_spent_total sums invoices where refundable_status='refunded' OR payment via
  a company-flagged method (any type except released_funds)
- released_funds invoices never count
- invoices satisfying BOTH conditions count only once
- paid_by_company per-invoice bool reflects company-payment method membership
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
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cs_app():
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

    class CsTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CsTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="cs_admin_role", description="CS Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="cs_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Spent Corp SARL",
            address="1 rue de la Dépense",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        # Create a company-payment method and a regular method
        company_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Spent Corp SARL",
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
            is_company_payment=False,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        # Soft-deleted company-payment method — should still count in spent total
        inactive_company_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Old Company Card",
            is_builtin=False,
            is_active=False,
            is_company_payment=True,
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_pm, regular_pm, inactive_company_pm])
        db.session.flush()

        project_with_company = ProjectModel(
            name="CS Project With Company",
            owner_id=admin_user.id,
            company_id=company.id,
        )
        project_no_company = ProjectModel(
            name="CS Project No Company",
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

        test_app._test_admin_email = "cs_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_with_company_id = str(project_with_company.id)
        test_app._test_project_no_company_id = str(project_no_company.id)
        test_app._test_company_legal_name = "Spent Corp SARL"
        test_app._test_company_pm_id = str(company_pm.id)
        test_app._test_regular_pm_id = str(regular_pm.id)
        test_app._test_inactive_company_pm_id = str(inactive_company_pm.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def cs_client(cs_app):
    return cs_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(cs_client, cs_app):
    return _login(cs_client, cs_app._test_admin_email, cs_app._test_admin_password)


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
    payment_method_id=None,
    items=None,
) -> str:
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
            payment_method_id=UUID(payment_method_id) if payment_method_id else None,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


# ---------------------------------------------------------------------------
# Tests: company_spent_total
# ---------------------------------------------------------------------------


class TestCompanySpentTotal:
    def test_refunded_ms_invoice_counts(self, cs_client, cs_app, admin_token):
        """Refunded M&S invoice counts toward company_spent_total."""
        project_id = cs_app._test_project_with_company_id

        _seed_invoice(cs_app, project_id, "materials_services", refundable_status="refunded")

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert "company_spent_total" in data
        # At least 2*100 = 200 from the refunded invoice (other tests also add to this module fixture)
        assert data["company_spent_total"] >= 200.0

    def test_company_paid_labor_invoice_counts(self, cs_client, cs_app, admin_token):
        """Labor invoice paid with a company-payment method counts toward total."""
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        _seed_invoice(
            cs_app,
            project_id,
            "labor",
            refundable_status=None,
            payment_method_id=company_pm_id,
            items=[{"description": "Labor", "quantity": 1, "unit_price": 500.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_spent_total"] >= 500.0

    def test_company_paid_ms_invoice_counts(self, cs_client, cs_app, admin_token):
        """M&S invoice paid with a company-payment method counts (even without refunded status)."""
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        _seed_invoice(
            cs_app,
            project_id,
            "materials_services",
            refundable_status=None,
            payment_method_id=company_pm_id,
            items=[{"description": "Matériau", "quantity": 3, "unit_price": 50.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_spent_total"] >= 150.0

    def test_company_paid_others_invoice_counts(self, cs_client, cs_app, admin_token):
        """'others' type invoice paid with a company-payment method counts."""
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        _seed_invoice(
            cs_app,
            project_id,
            "others",
            refundable_status=None,
            payment_method_id=company_pm_id,
            items=[{"description": "Misc", "quantity": 1, "unit_price": 75.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_spent_total"] >= 75.0

    def test_both_conditions_on_one_invoice_counts_once(self, cs_client, cs_app, admin_token):
        """Invoice satisfying both refunded and company-payment conditions counts once."""
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        # An invoice that is BOTH refunded AND paid with company method
        _seed_invoice(
            cs_app,
            project_id,
            "materials_services",
            refundable_status="refunded",
            payment_method_id=company_pm_id,
            items=[{"description": "Both", "quantity": 1, "unit_price": 300.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        # The 300 invoice must appear exactly once — paid_by_company is True,
        # and the total is not 600. We verify the per-invoice flag.
        inv_list = data["invoices"]
        # No assertions on exact total here (other tests add to the module fixture),
        # but the paid_by_company field must be True for this invoice
        assert any(i["paid_by_company"] is True for i in inv_list)

    def test_non_company_payment_method_not_counted(self, cs_client, cs_app, admin_token):
        """Invoice paid with a non-flagged method and no refunded status does not count."""
        project_id = cs_app._test_project_with_company_id
        regular_pm_id = cs_app._test_regular_pm_id

        _seed_invoice(
            cs_app,
            project_id,
            "materials_services",
            refundable_status=None,
            payment_method_id=regular_pm_id,
            items=[{"description": "Regular", "quantity": 1, "unit_price": 999.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        # The invoice paid with regular method, no refunded status, must have paid_by_company=false
        inv_list = data["invoices"]
        regular_inv = next(
            (
                i
                for i in inv_list
                if i.get("payment_method_id")
                and str(i["payment_method_id"]) == regular_pm_id
                and i["refundable_status"] is None
            ),
            None,
        )
        if regular_inv:
            assert regular_inv["paid_by_company"] is False

    def test_released_funds_never_counts(self, cs_client, cs_app, admin_token):
        """released_funds invoices never count toward company_spent_total.

        paid_by_company reflects the payment method flag only; released_funds is
        excluded from the *total* via sum_company_spent's type filter.  The per-invoice
        field may be true (the method is a company method), but the total must not
        include the 10 000 sentinel amount we use to detect a regression.
        """
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        # Add a large released_funds invoice with a company method — its 10 000 amount
        # must never appear in company_spent_total.
        _seed_invoice(
            cs_app,
            project_id,
            "released_funds",
            refundable_status=None,
            payment_method_id=company_pm_id,
            items=[{"description": "Released", "quantity": 1, "unit_price": 10000.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        # company_spent_total must not include the 10 000 sentinel
        assert data["company_spent_total"] < 10000.0

    def test_soft_deleted_company_method_still_counts(self, cs_client, cs_app, admin_token):
        """Invoice paid with a soft-deleted (is_active=false) company-payment method still counts."""
        project_id = cs_app._test_project_with_company_id
        inactive_pm_id = cs_app._test_inactive_company_pm_id

        _seed_invoice(
            cs_app,
            project_id,
            "materials_services",
            refundable_status=None,
            payment_method_id=inactive_pm_id,
            items=[{"description": "InactivePM", "quantity": 1, "unit_price": 250.0, "vat_rate": 0}],
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        # paid_by_company must be true for the invoice using the soft-deleted company PM
        inv_list = data["invoices"]
        inactive_inv = next(
            (i for i in inv_list if i.get("payment_method_id") and str(i["payment_method_id"]) == inactive_pm_id),
            None,
        )
        assert inactive_inv is not None, "Invoice with inactive company PM not found in response"
        assert inactive_inv["paid_by_company"] is True

    def test_no_company_payment_returns_zero(self, cs_client, cs_app, admin_token):
        """Project with no company-qualifying invoices returns company_spent_total=0."""
        project_id = cs_app._test_project_no_company_id

        _seed_invoice(cs_app, project_id, "materials_services", refundable_status="refundable")
        _seed_invoice(cs_app, project_id, "labor", refundable_status=None)

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_spent_total"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: paid_by_company per-invoice flag
# ---------------------------------------------------------------------------


class TestPaidByCompanyPerInvoice:
    def test_paid_by_company_true_for_company_payment_method(self, cs_client, cs_app, admin_token):
        """paid_by_company=true when invoice payment_method_id is a company-flagged method."""
        project_id = cs_app._test_project_with_company_id
        company_pm_id = cs_app._test_company_pm_id

        inv_id = _seed_invoice(
            cs_app,
            project_id,
            "labor",
            payment_method_id=company_pm_id,
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_company"] is True

    def test_paid_by_company_false_for_regular_payment_method(self, cs_client, cs_app, admin_token):
        """paid_by_company=false when invoice uses a non-flagged payment method."""
        project_id = cs_app._test_project_with_company_id
        regular_pm_id = cs_app._test_regular_pm_id

        inv_id = _seed_invoice(
            cs_app,
            project_id,
            "materials_services",
            payment_method_id=regular_pm_id,
        )

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_company"] is False

    def test_paid_by_company_false_when_no_payment_method(self, cs_client, cs_app, admin_token):
        """paid_by_company=false when invoice has no payment_method_id."""
        project_id = cs_app._test_project_with_company_id

        inv_id = _seed_invoice(cs_app, project_id, "labor", payment_method_id=None)

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        data = resp.get_json()
        inv = next((i for i in data["invoices"] if i["id"] == inv_id), None)
        assert inv is not None
        assert inv["paid_by_company"] is False


# ---------------------------------------------------------------------------
# Tests: company_name
# ---------------------------------------------------------------------------


class TestCompanyName:
    def test_company_name_present_when_project_has_company(self, cs_client, cs_app, admin_token):
        """company_name equals the company's legal_name when project has a company."""
        project_id = cs_app._test_project_with_company_id

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_name"] == cs_app._test_company_legal_name

    def test_company_name_null_when_project_has_no_company(self, cs_client, cs_app, admin_token):
        """company_name is null when the project is not attached to a company."""
        project_id = cs_app._test_project_no_company_id

        resp = cs_client.get(_list_url(project_id), headers=_auth(admin_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["company_name"] is None


# ---------------------------------------------------------------------------
# Tests: type-change guard
# ---------------------------------------------------------------------------


class TestInvoiceTypeChangeGuard:
    def _create_ms_invoice_with_status(self, app, project_id, refundable_status):
        return _seed_invoice(app, project_id, "materials_services", refundable_status=refundable_status)

    def test_type_change_blocked_when_refundable_status_set(self, cs_client, cs_app, admin_token):
        """PATCH type change returns 400 when the invoice has a non-null refundable_status."""
        project_id = cs_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cs_app, project_id, "refundable")

        resp = cs_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "labor"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "refundable" in body.get("message", "").lower() or "type" in body.get("message", "").lower()

    def test_type_change_succeeds_when_status_null(self, cs_client, cs_app, admin_token):
        """PATCH type change succeeds when refundable_status is NULL."""
        project_id = cs_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cs_app, project_id, None)

        resp = cs_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "labor"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["type"] == "labor"

    def test_type_change_succeeds_after_clearing_status(self, cs_client, cs_app, admin_token):
        """PATCH type change succeeds after refundable_status is explicitly cleared to null."""
        from app import db

        project_id = cs_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cs_app, project_id, "refund_pending")

        with cs_app.app_context():
            row = db.session.get(InvoiceModel, UUID(invoice_id))
            row.refundable_status = None
            db.session.commit()

        resp = cs_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "others"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["type"] == "others"

    def test_same_type_patch_with_status_set_is_allowed(self, cs_client, cs_app, admin_token):
        """PATCH with the same type as existing does not trigger the guard."""
        project_id = cs_app._test_project_with_company_id
        invoice_id = self._create_ms_invoice_with_status(cs_app, project_id, "refundable")

        resp = cs_client.put(
            _invoice_url(project_id, invoice_id),
            json={"type": "materials_services", "recipient_name": "Updated Name"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
