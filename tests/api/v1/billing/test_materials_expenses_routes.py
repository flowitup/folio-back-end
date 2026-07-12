"""API integration tests for materials & services expenses endpoints.

Covers:
  GET  /api/v1/billing/materials-expenses
  PATCH /api/v1/billing/materials-expenses/<invoice_id>

Test scenarios:
  - Company admin sees M&S invoices from both their projects
  - Invoice from a different company excluded
  - Project with company_id=NULL excluded
  - refundable=false returns only NULL-status invoices
  - refundable=true returns only non-null invoices
  - PATCH sets status, appears under refundable=true
  - PATCH null clears status, moves to refundable=false
  - Free-form status transitions both directions
  - Invalid status value → 400
  - Invoice of wrong type (labor) → 400
  - Non-admin → 403 on GET and PATCH
  - Superadmin sees all companies
  - project_name present in list response
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app import db
from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(
    project_id: UUID,
    user_id: UUID,
    invoice_type: str = "materials_services",
    refundable_status: str | None = None,
    refunded_by: str | None = None,
    quantity: float = 2.0,
    unit_price: float = 500.0,
) -> InvoiceModel:
    """Build and persist an InvoiceModel row directly (bypasses use-case)."""
    now = datetime.now(timezone.utc)
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=f"INV-{uuid4().hex[:8]}",
        type=invoice_type,
        issue_date=date.today(),
        recipient_name="Test Supplier",
        items=[{"description": "Materials", "quantity": quantity, "unit_price": unit_price}],
        created_by=user_id,
        created_at=now,
        updated_at=now,
        refundable_status=refundable_status,
        refunded_by=refunded_by,
    )
    db.session.add(inv)
    db.session.commit()
    return inv


# ---------------------------------------------------------------------------
# Module-scoped app + fixture setup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mat_exp_app():
    """Flask app with in-memory SQLite wired for materials-expenses tests."""
    from app import create_app
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.application.invoice.list_materials_expenses_usecase import ListMaterialsExpensesUseCase
    from app.application.invoice.set_refundable_status_usecase import SetInvoiceRefundableStatusUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class MatExpTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(MatExpTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        # Permissions
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        inv_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        superadmin_role = RoleModel(name="mat_exp_superadmin", description="Superadmin")
        superadmin_role.permissions.append(star_perm)

        member_role = RoleModel(name="mat_exp_member", description="Member")
        member_role.permissions.append(read_perm)
        member_role.permissions.append(inv_perm)

        db.session.add_all([star_perm, read_perm, inv_perm, superadmin_role, member_role])
        db.session.flush()

        # Users
        admin_user = UserModel(
            email="mat_exp_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(superadmin_role)  # superadmin so JWT has "*:*"

        non_admin_user = UserModel(
            email="mat_exp_nonadmin@test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        non_admin_user.roles.append(member_role)

        # Plain company-A admin: has member_role (no *:*), will get company-admin
        # access row for company_a only — used to exercise non-superadmin code path.
        plain_company_a_admin_user = UserModel(
            email="mat_exp_company_a_admin@test.com",
            password_hash=hasher.hash("CompanyA1234!"),
            is_active=True,
        )
        plain_company_a_admin_user.roles.append(member_role)

        db.session.add_all([admin_user, non_admin_user, plain_company_a_admin_user])
        db.session.flush()

        now = datetime.now(timezone.utc)

        # Company A (admin_user is company-admin)
        company_a = CompanyModel(
            id=uuid4(),
            legal_name="Company A SARL",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        # Company B (admin_user has no access)
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

        # Projects
        project_a1 = ProjectModel(name="Project A1", owner_id=admin_user.id, company_id=company_a.id)
        project_a2 = ProjectModel(name="Project A2", owner_id=admin_user.id, company_id=company_a.id)
        project_b = ProjectModel(name="Project B", owner_id=non_admin_user.id, company_id=company_b.id)
        project_no_company = ProjectModel(name="No Company Project", owner_id=admin_user.id, company_id=None)

        db.session.add_all([project_a1, project_a2, project_b, project_no_company])
        db.session.commit()

        # UserCompanyAccess — admin_user is admin of company_a only
        from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

        access_a = UserCompanyAccessModel(
            user_id=admin_user.id,
            company_id=company_a.id,
            role="admin",
            is_primary=True,
            attached_at=now,
        )
        # plain_company_a_admin_user is admin of company_a ONLY (no *:* permission)
        plain_access_a = UserCompanyAccessModel(
            user_id=plain_company_a_admin_user.id,
            company_id=company_a.id,
            role="admin",
            is_primary=True,
            attached_at=now,
        )
        db.session.add_all([access_a, plain_access_a])
        db.session.commit()

        # Repos
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)
        company_repo = SqlAlchemyCompanyRepository(db.session)
        access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo

        _c.list_materials_expenses_usecase = ListMaterialsExpensesUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
        )
        _c.set_refundable_status_usecase = SetInvoiceRefundableStatusUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
        )

        # Store IDs on app for fixture use
        test_app._admin_user_id = admin_user.id
        test_app._non_admin_user_id = non_admin_user.id
        test_app._plain_company_a_admin_user_id = plain_company_a_admin_user.id
        test_app._company_a_id = company_a.id
        test_app._company_b_id = company_b.id
        test_app._project_a1_id = project_a1.id
        test_app._project_a2_id = project_a2.id
        test_app._project_b_id = project_b.id
        test_app._project_no_company_id = project_no_company.id

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def mat_client(mat_exp_app):
    return mat_exp_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def admin_tok(mat_client):
    return _login(mat_client, "mat_exp_admin@test.com", "Admin1234!")


@pytest.fixture(scope="module")
def nonadmin_tok(mat_client):
    return _login(mat_client, "mat_exp_nonadmin@test.com", "Member1234!")


@pytest.fixture(scope="module")
def plain_company_a_admin_tok(mat_client):
    """JWT for a user who is company-A admin but has NO *:* superadmin permission."""
    return _login(mat_client, "mat_exp_company_a_admin@test.com", "CompanyA1234!")


# ---------------------------------------------------------------------------
# Aggregation: invoices from both company-A projects returned
# ---------------------------------------------------------------------------


class TestListAggregation:
    def test_invoices_from_two_projects_returned(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv1 = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv2 = _make_invoice(mat_exp_app._project_a2_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv1_id, inv2_id = str(inv1.id), str(inv2.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv1_id in ids
        assert inv2_id in ids

    def test_invoice_from_other_company_excluded_for_non_superadmin(self, mat_client, nonadmin_tok, mat_exp_app):
        """A company-admin of company-A should NOT see company-B invoices.

        Uses nonadmin_tok — a user with no company-admin role at all,
        so their accessible company list is empty and result is empty too.
        This verifies the scoping boundary (no bleed between companies).
        """
        with mat_exp_app.app_context():
            _make_invoice(mat_exp_app._project_b_id, mat_exp_app._non_admin_user_id, refundable_status="refundable")

        # nonadmin_tok has no company-admin access → empty list, no 403
        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(nonadmin_tok),
        )
        assert resp.status_code == 200
        # Should be empty — non-admin with no admin companies sees nothing
        assert resp.get_json()["total"] == 0

    def test_project_with_null_company_excluded(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv_nc = _make_invoice(
                mat_exp_app._project_no_company_id, mat_exp_app._admin_user_id, refundable_status="refundable"
            )
            inv_nc_id = str(inv_nc.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_nc_id not in ids

    def test_project_name_present_in_response(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) >= 1
        for item in items:
            assert "project_name" in item
            assert item["project_name"] is not None


# ---------------------------------------------------------------------------
# Refundable filter
# ---------------------------------------------------------------------------


class TestRefundableFilter:
    def test_refundable_false_returns_only_null_status(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv_null = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status=None)
            inv_set = _make_invoice(
                mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable"
            )
            inv_null_id, inv_set_id = str(inv_null.id), str(inv_set.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=false",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_null_id in ids
        assert inv_set_id not in ids

    def test_refundable_true_returns_only_non_null_status(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv_null = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status=None)
            inv_set = _make_invoice(
                mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refund_pending"
            )
            inv_null_id, inv_set_id = str(inv_null.id), str(inv_set.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_null_id not in ids
        assert inv_set_id in ids


# ---------------------------------------------------------------------------
# PATCH set / clear / transition
# ---------------------------------------------------------------------------


class TestPatchRefundableStatus:
    def test_set_status_persists_and_appears_in_refundable_true(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status=None)
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] == "refundable"

        # Now appears under refundable=true
        list_resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        ids = [i["id"] for i in list_resp.get_json()["items"]]
        assert inv_id in ids

    def test_clear_status_moves_to_refundable_false(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        # Clear status by sending null
        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": None},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] is None

        # Now appears under refundable=false
        list_resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=false",
            headers=_auth(admin_tok),
        )
        ids = [i["id"] for i in list_resp.get_json()["items"]]
        assert inv_id in ids

        # Absent from refundable=true
        list_true = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        ids_true = [i["id"] for i in list_true.get_json()["items"]]
        assert inv_id not in ids_true

    def test_transition_from_refundable_to_refunded(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refunded"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["refundable_status"] == "refunded"

    def test_transition_to_refund_pending(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refund_pending"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        assert resp.get_json()["refundable_status"] == "refund_pending"

    def test_refunded_with_bank_persists_refunded_by(self, mat_client, admin_tok, mat_exp_app):
        """PATCH {refundable_status:'refunded', refunded_by:'bank'} → 200 + refunded_by in response."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refunded", "refunded_by": "bank"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["refundable_status"] == "refunded"
        assert body["refunded_by"] == "bank"

    def test_refunded_without_refunded_by_defaults_to_company(self, mat_client, admin_tok, mat_exp_app):
        """PATCH {refundable_status:'refunded'} with refunded_by omitted → defaults to 'company'."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refunded"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["refundable_status"] == "refunded"
        assert body["refunded_by"] == "company"

    def test_refunded_by_cleared_when_status_moves_off_refunded(self, mat_client, admin_tok, mat_exp_app):
        """Moving off 'refunded' forces refunded_by back to null even if a value is sent."""
        with mat_exp_app.app_context():
            inv = _make_invoice(
                mat_exp_app._project_a1_id,
                mat_exp_app._admin_user_id,
                refundable_status="refunded",
                refunded_by="bank",
            )
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refundable", "refunded_by": "bank"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["refundable_status"] == "refundable"
        assert body["refunded_by"] is None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_invalid_status_value_returns_400(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id)
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "totally_invalid"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 400

    def test_invalid_refunded_by_value_returns_400(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id)
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refunded", "refunded_by": "cash"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 400

    def test_labor_invoice_returns_400(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(
                mat_exp_app._project_a1_id,
                mat_exp_app._admin_user_id,
                invoice_type="labor",
            )
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 400

    def test_nonexistent_invoice_returns_404(self, mat_client, admin_tok):
        fake_id = str(uuid4())
        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{fake_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, mat_client, admin_tok):
        resp = mat_client.patch(
            "/api/v1/billing/materials-expenses/not-a-uuid",
            json={"refundable_status": "refundable"},
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Authorization: non-admin → 403
# ---------------------------------------------------------------------------


class TestAuthorizationGate:
    def test_non_admin_get_returns_403_on_company_filter(self, mat_client, nonadmin_tok, mat_exp_app):
        """Non-admin requesting a specific company they don't admin → 403."""
        resp = mat_client.get(
            f"/api/v1/billing/materials-expenses?company_id={mat_exp_app._company_a_id}",
            headers=_auth(nonadmin_tok),
        )
        assert resp.status_code == 403

    def test_non_admin_patch_returns_403(self, mat_client, nonadmin_tok, mat_exp_app):
        """Non-admin cannot set refundable_status on company-A invoice."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id)
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(nonadmin_tok),
        )
        assert resp.status_code == 403

    def test_unauthenticated_get_returns_401(self, mat_exp_app):
        # Use a fresh client with no cookies to avoid module-scoped session leakage
        fresh = mat_exp_app.test_client()
        resp = fresh.get("/api/v1/billing/materials-expenses")
        assert resp.status_code == 401

    def test_unauthenticated_patch_returns_401(self, mat_exp_app):
        fresh = mat_exp_app.test_client()
        resp = fresh.patch(
            f"/api/v1/billing/materials-expenses/{uuid4()}",
            json={"refundable_status": "refundable"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Superadmin sees all companies
# ---------------------------------------------------------------------------


class TestSuperadminScope:
    def test_superadmin_sees_company_b_invoices(self, mat_client, admin_tok, mat_exp_app):
        """The admin_tok user has *:* → superadmin; should see invoices from company B."""
        with mat_exp_app.app_context():
            inv_b = _make_invoice(
                mat_exp_app._project_b_id, mat_exp_app._non_admin_user_id, refundable_status="refundable"
            )
            inv_b_id = str(inv_b.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_b_id in ids

    def test_superadmin_company_id_filter_scopes_correctly(self, mat_client, admin_tok, mat_exp_app):
        """Superadmin with company_id filter sees only that company's invoices."""
        with mat_exp_app.app_context():
            inv_a = _make_invoice(
                mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable"
            )
            inv_b = _make_invoice(
                mat_exp_app._project_b_id, mat_exp_app._non_admin_user_id, refundable_status="refundable"
            )
            inv_a_id, inv_b_id = str(inv_a.id), str(inv_b.id)

        resp = mat_client.get(
            f"/api/v1/billing/materials-expenses?refundable=true&company_id={mat_exp_app._company_a_id}",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_a_id in ids
        assert inv_b_id not in ids


# ---------------------------------------------------------------------------
# H1: Plain company-A admin (non-superadmin) authz + IDOR guard
# ---------------------------------------------------------------------------


class TestPlainCompanyAdminAuthz:
    """Exercises the non-superadmin branch and IDOR guard.

    The plain_company_a_admin_tok user has role='admin' on company A only,
    with NO *:* permission in their JWT — so every request goes through the
    admin_company_ids() resolution path, not the superadmin bypass.
    """

    def test_plain_admin_patch_own_company_invoice_returns_200(
        self, mat_client, plain_company_a_admin_tok, mat_exp_app
    ):
        """Plain company-A admin can PATCH a company-A invoice → 200, status persists."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._plain_company_a_admin_user_id)
            inv_id = str(inv.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(plain_company_a_admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refundable_status"] == "refundable"

    def test_plain_admin_patch_other_company_invoice_denied_idor(
        self, mat_client, plain_company_a_admin_tok, mat_exp_app
    ):
        """Plain company-A admin PATCHing a company-B invoice must be DENIED (IDOR guard)."""
        with mat_exp_app.app_context():
            inv_b = _make_invoice(mat_exp_app._project_b_id, mat_exp_app._non_admin_user_id)
            inv_b_id = str(inv_b.id)

        resp = mat_client.patch(
            f"/api/v1/billing/materials-expenses/{inv_b_id}",
            json={"refundable_status": "refundable"},
            headers=_auth(plain_company_a_admin_tok),
        )
        # Must NOT succeed — 403 (or 404 if the implementation raises NotFound first)
        assert resp.status_code in (403, 404), f"IDOR guard failed: expected 403/404 but got {resp.status_code}"

    def test_plain_admin_get_returns_only_own_company_invoices(
        self, mat_client, plain_company_a_admin_tok, mat_exp_app
    ):
        """Plain company-A admin GET must return only company-A rows, never company-B."""
        with mat_exp_app.app_context():
            inv_a = _make_invoice(
                mat_exp_app._project_a1_id,
                mat_exp_app._plain_company_a_admin_user_id,
                refundable_status="refundable",
            )
            inv_b = _make_invoice(
                mat_exp_app._project_b_id,
                mat_exp_app._non_admin_user_id,
                refundable_status="refundable",
            )
            inv_a_id, inv_b_id = str(inv_a.id), str(inv_b.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(plain_company_a_admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        ids = [i["id"] for i in resp.get_json()["items"]]
        assert inv_a_id in ids, "company-A invoice must be visible to company-A admin"
        assert inv_b_id not in ids, "company-B invoice must NOT leak to company-A admin"

    def test_plain_admin_get_with_foreign_company_id_returns_403(
        self, mat_client, plain_company_a_admin_tok, mat_exp_app
    ):
        """Plain company-A admin GET with ?company_id=<company-B> they can't admin → 403."""
        resp = mat_client.get(
            f"/api/v1/billing/materials-expenses?company_id={mat_exp_app._company_b_id}",
            headers=_auth(plain_company_a_admin_tok),
        )
        assert resp.status_code == 403, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# M2: Rate-limit on GET endpoint
# ---------------------------------------------------------------------------


class TestGetRateLimit:
    def test_get_endpoint_returns_200_under_normal_use(self, mat_client, admin_tok, mat_exp_app):
        """GET /billing/materials-expenses is reachable and rate-limit is not tripped by one call."""
        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Inline attachments in materials-expenses response
# ---------------------------------------------------------------------------


def _make_attachment(invoice_id: UUID, user_id: UUID, filename: str = "receipt.pdf") -> InvoiceAttachmentModel:
    """Persist an InvoiceAttachmentModel row bypassing the upload use-case."""
    from datetime import datetime, timezone

    att = InvoiceAttachmentModel(
        id=uuid4(),
        # FK columns must be UUID objects (not strings) for SQLite PG_UUID compat
        invoice_id=invoice_id,
        filename=filename,
        storage_key=f"test/{uuid4().hex}/{filename}",
        mime_type="application/pdf",
        size_bytes=1024,
        uploaded_by=user_id,
        uploaded_at=datetime.now(timezone.utc),
    )
    db.session.add(att)
    db.session.commit()
    return att


class TestInlineAttachments:
    """Inline attachments field in GET /billing/materials-expenses response."""

    def test_attachments_empty_list_when_invoice_has_none(self, mat_client, admin_tok, mat_exp_app):
        """An invoice with no attachments returns attachments=[] (not missing key)."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["items"]}
        assert inv_id in items
        assert items[inv_id]["attachments"] == []

    def test_attachments_populated_for_invoice_with_two_attachments(self, mat_client, admin_tok, mat_exp_app):
        """An invoice with 2 attachments returns both with correct metadata fields."""
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            att1 = _make_attachment(inv.id, mat_exp_app._admin_user_id, "invoice_a.pdf")
            att2 = _make_attachment(inv.id, mat_exp_app._admin_user_id, "receipt_b.pdf")
            inv_id = str(inv.id)
            att1_id, att2_id = str(att1.id), str(att2.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["items"]}
        assert inv_id in items
        attachments = items[inv_id]["attachments"]
        assert len(attachments) == 2
        attachment_ids = {a["id"] for a in attachments}
        assert att1_id in attachment_ids
        assert att2_id in attachment_ids
        # Every attachment must carry the required metadata fields
        for a in attachments:
            assert "id" in a
            assert "filename" in a
            assert "mime_type" in a
            assert "size_bytes" in a

    def test_attachments_of_other_invoices_not_leaked(self, mat_client, admin_tok, mat_exp_app):
        """Attachments from other invoices must NOT appear on a different invoice's row."""
        with mat_exp_app.app_context():
            inv_a = _make_invoice(
                mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable"
            )
            inv_b = _make_invoice(
                mat_exp_app._project_a2_id, mat_exp_app._admin_user_id, refundable_status="refundable"
            )
            # Attach to inv_b only
            att_b = _make_attachment(inv_b.id, mat_exp_app._admin_user_id, "only_b.pdf")
            inv_a_id, inv_b_id = str(inv_a.id), str(inv_b.id)
            att_b_id = str(att_b.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["items"]}
        assert inv_a_id in items
        assert inv_b_id in items
        # att_b must appear on inv_b, not on inv_a
        inv_a_att_ids = {a["id"] for a in items[inv_a_id]["attachments"]}
        inv_b_att_ids = {a["id"] for a in items[inv_b_id]["attachments"]}
        assert att_b_id not in inv_a_att_ids
        assert att_b_id in inv_b_att_ids


# ---------------------------------------------------------------------------
# has_bank_refund flag (refunded by bank = ≥1 linked refund invoice)
# ---------------------------------------------------------------------------


def _make_linked_refund(source_id: UUID, project_id: UUID, user_id: UUID) -> InvoiceModel:
    """Persist a type='refund' invoice linked to source_id via refunds_invoice_id."""
    now = datetime.now(timezone.utc)
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=f"REF-{uuid4().hex[:8]}",
        type="refund",
        issue_date=date.today(),
        recipient_name="Refund",
        items=[{"description": "Refund", "quantity": 1.0, "unit_price": -100.0}],
        created_by=user_id,
        created_at=now,
        updated_at=now,
        refunds_invoice_id=source_id,
    )
    db.session.add(inv)
    db.session.commit()
    return inv


class TestHasBankRefundFlag:
    def test_expense_with_linked_refund_has_bank_refund_true(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refunded")
            _make_linked_refund(inv.id, mat_exp_app._project_a1_id, mat_exp_app._admin_user_id)
            inv_id = str(inv.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["items"]}
        assert items[inv_id]["has_bank_refund"] is True

    def test_expense_without_linked_refund_has_bank_refund_false(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            inv = _make_invoice(mat_exp_app._project_a1_id, mat_exp_app._admin_user_id, refundable_status="refundable")
            inv_id = str(inv.id)

        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["items"]}
        assert items[inv_id]["has_bank_refund"] is False


# ---------------------------------------------------------------------------
# GET summary aggregate (refundable_amount / refunded_total / refunded_by_*)
# ---------------------------------------------------------------------------


class TestRefundSummary:
    """Summary is aggregated over the FULL filter set (company scope), not the page.

    Each test creates its own company + project so seeded totals aren't polluted
    by invoices created in other test classes within this module-scoped fixture.
    admin_tok is superadmin, so ?company_id=<new company> scopes the query exactly
    to that company without needing a UserCompanyAccess row.
    """

    def _make_summary_company_and_project(self, mat_exp_app):
        from uuid import uuid4 as _uuid4

        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=_uuid4(),
            legal_name="Summary Test Co",
            address="3 rue de la Paix",
            created_by=mat_exp_app._admin_user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()
        project = ProjectModel(name="Summary Project", owner_id=mat_exp_app._admin_user_id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return company, project

    def test_summary_matches_seeded_mix(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            company, project = self._make_summary_company_and_project(mat_exp_app)
            # refundable: 1 x 100 = 100
            _make_invoice(
                project.id, mat_exp_app._admin_user_id, refundable_status="refundable", quantity=1, unit_price=100
            )
            # refund_pending: 1 x 50 = 50
            _make_invoice(
                project.id,
                mat_exp_app._admin_user_id,
                refundable_status="refund_pending",
                quantity=1,
                unit_price=50,
            )
            # refunded, refunded_by=None → counts as company: 1 x 200 = 200
            _make_invoice(
                project.id,
                mat_exp_app._admin_user_id,
                refundable_status="refunded",
                refunded_by=None,
                quantity=1,
                unit_price=200,
            )
            # refunded, refunded_by='bank': 1 x 75 = 75
            _make_invoice(
                project.id,
                mat_exp_app._admin_user_id,
                refundable_status="refunded",
                refunded_by="bank",
                quantity=1,
                unit_price=75,
            )
            company_id = str(company.id)

        resp = mat_client.get(
            f"/api/v1/billing/materials-expenses?refundable=true&company_id={company_id}",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        summary = resp.get_json()["summary"]
        assert summary is not None
        assert summary["refundable_amount"] == pytest.approx(150.0)
        assert summary["refunded_total"] == pytest.approx(275.0)
        assert summary["refunded_by_company"] == pytest.approx(200.0)
        assert summary["refunded_by_bank"] == pytest.approx(75.0)

    def test_summary_null_when_refundable_false(self, mat_client, admin_tok, mat_exp_app):
        with mat_exp_app.app_context():
            company, project = self._make_summary_company_and_project(mat_exp_app)
            _make_invoice(project.id, mat_exp_app._admin_user_id, refundable_status=None)
            company_id = str(company.id)

        resp = mat_client.get(
            f"/api/v1/billing/materials-expenses?refundable=false&company_id={company_id}",
            headers=_auth(admin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["summary"] is None

    def test_summary_zeroed_for_non_admin_with_no_admin_companies(self, mat_client, nonadmin_tok):
        """Non-admin with zero admin companies gets a zeroed summary on refundable=true."""
        resp = mat_client.get(
            "/api/v1/billing/materials-expenses?refundable=true",
            headers=_auth(nonadmin_tok),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        summary = resp.get_json()["summary"]
        assert summary == {
            "refundable_amount": 0.0,
            "refunded_total": 0.0,
            "refunded_by_company": 0.0,
            "refunded_by_bank": 0.0,
        }
