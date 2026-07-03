"""API integration tests for invoice service_month (labor payment-month tracking).

Covers:
- create labor invoice with service_month → normalized to first-of-month
- create labor invoice without service_month → null in response
- create materials_services invoice with service_month → 400 service_month_not_allowed
- PATCH-only service_month on a labor invoice → updated; all other fields unchanged
- PATCH service_month=null → cleared
- PATCH type labor→others without touching service_month → cleared server-side
- list + get responses include service_month
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inv_sm_app():
    """Flask app wired with invoice use-cases for service_month tests."""
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

    class InvSmTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InvSmTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="inv_sm_admin", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="inv_sm_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Service Month Co",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(
            name="Service Month Test Project",
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
        # Mirror production wiring shape (create/update use-cases take the invoice
        # repo plus optional payment_method_repo / tag_repo — neither is exercised
        # by these tests, so they are left at their default None).
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)

        test_app._test_admin_email = "inv_sm_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_sm_client(inv_sm_app):
    return inv_sm_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_sm_client, inv_sm_app):
    return _login(inv_sm_client, inv_sm_app._test_admin_email, inv_sm_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _labor_invoice_body(**overrides):
    body = {
        "type": "labor",
        "issue_date": date.today().isoformat(),
        "recipient_name": "Worker Payroll",
        "notes": "Original notes",
        "items": [{"description": "Labor", "quantity": 1, "unit_price": 100}],
    }
    body.update(overrides)
    return body


def _create_invoice_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateInvoiceServiceMonth:
    def test_labor_invoice_service_month_normalized_to_first_of_month(self, inv_sm_client, inv_sm_app, admin_token):
        body = _labor_invoice_body(service_month="2026-06-15")
        resp = inv_sm_client.post(
            _create_invoice_url(inv_sm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["service_month"] == "2026-06-01"

    def test_labor_invoice_without_service_month_is_null(self, inv_sm_client, inv_sm_app, admin_token):
        resp = inv_sm_client.post(
            _create_invoice_url(inv_sm_app._test_project_id),
            json=_labor_invoice_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["service_month"] is None

    def test_materials_services_with_service_month_returns_400(self, inv_sm_client, inv_sm_app, admin_token):
        body = {
            "type": "materials_services",
            "issue_date": date.today().isoformat(),
            "recipient_name": "Supplier Co",
            "items": [{"description": "Materials", "quantity": 1, "unit_price": 50}],
            "service_month": "2026-06-01",
        }
        resp = inv_sm_client.post(
            _create_invoice_url(inv_sm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "service_month_not_allowed"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdateInvoiceServiceMonth:
    def _create_labor_invoice(self, client, app, token, **overrides):
        body = _labor_invoice_body(**overrides)
        resp = client.post(
            _create_invoice_url(app._test_project_id),
            json=body,
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()

    def test_patch_only_service_month_leaves_other_fields_unchanged(self, inv_sm_client, inv_sm_app, admin_token):
        created = self._create_labor_invoice(
            inv_sm_client,
            inv_sm_app,
            admin_token,
            recipient_name="Original Recipient",
            notes="Keep me",
        )
        invoice_id = created["id"]

        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"service_month": "2026-03-10"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["service_month"] == "2026-03-01"
        # All other fields must be untouched.
        assert data["recipient_name"] == "Original Recipient"
        assert data["notes"] == "Keep me"
        assert data["items"] == created["items"]
        assert data["tag_id"] == created["tag_id"]
        assert data["type"] == "labor"

    def test_patch_service_month_null_clears_it(self, inv_sm_client, inv_sm_app, admin_token):
        created = self._create_labor_invoice(inv_sm_client, inv_sm_app, admin_token, service_month="2026-05-01")
        invoice_id = created["id"]
        assert created["service_month"] == "2026-05-01"

        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"service_month": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["service_month"] is None

    def test_patch_type_away_from_labor_clears_stored_service_month(self, inv_sm_client, inv_sm_app, admin_token):
        created = self._create_labor_invoice(inv_sm_client, inv_sm_app, admin_token, service_month="2026-04-01")
        invoice_id = created["id"]
        assert created["service_month"] == "2026-04-01"

        # PATCH changes type away from labor without touching service_month in the payload.
        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"type": "others"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type"] == "others"
        assert data["service_month"] is None

    def test_patch_setting_service_month_on_non_labor_invoice_returns_400(self, inv_sm_client, inv_sm_app, admin_token):
        body = {
            "type": "materials_services",
            "issue_date": date.today().isoformat(),
            "recipient_name": "Supplier Co",
            "items": [{"description": "Materials", "quantity": 1, "unit_price": 50}],
        }
        resp_create = inv_sm_client.post(
            _create_invoice_url(inv_sm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp_create.status_code == 201
        invoice_id = resp_create.get_json()["id"]

        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"service_month": "2026-06-01"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "service_month_not_allowed"

    def test_patch_type_away_from_labor_with_service_month_in_payload_returns_400(
        self, inv_sm_client, inv_sm_app, admin_token
    ):
        created = self._create_labor_invoice(inv_sm_client, inv_sm_app, admin_token, service_month="2026-03-01")
        invoice_id = created["id"]

        # Switching away from labor while explicitly supplying a month must be rejected:
        # the resulting invoice would be non-labor with a payment month.
        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"type": "others", "service_month": "2026-03-01"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "service_month_not_allowed"

        # Invoice unchanged by the rejected PATCH.
        resp_get = inv_sm_client.get(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            headers=_auth(admin_token),
        )
        assert resp_get.get_json()["type"] == "labor"
        assert resp_get.get_json()["service_month"] == "2026-03-01"

    def test_patch_type_away_from_labor_with_explicit_null_service_month_clears_it(
        self, inv_sm_client, inv_sm_app, admin_token
    ):
        created = self._create_labor_invoice(inv_sm_client, inv_sm_app, admin_token, service_month="2026-03-01")
        invoice_id = created["id"]

        resp = inv_sm_client.put(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            json={"type": "others", "service_month": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type"] == "others"
        assert data["service_month"] is None


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


class TestInvoiceListGetIncludeServiceMonth:
    def test_list_and_get_include_service_month(self, inv_sm_client, inv_sm_app, admin_token):
        marker = "SvcMonthMarker-" + uuid4().hex[:8]
        body = _labor_invoice_body(recipient_name=marker, service_month="2026-07-20")
        resp_create = inv_sm_client.post(
            _create_invoice_url(inv_sm_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp_create.status_code == 201
        invoice_id = resp_create.get_json()["id"]

        resp_list = inv_sm_client.get(
            _create_invoice_url(inv_sm_app._test_project_id),
            headers=_auth(admin_token),
        )
        assert resp_list.status_code == 200
        matching = [i for i in resp_list.get_json()["invoices"] if i["recipient_name"] == marker]
        assert len(matching) == 1
        assert matching[0]["service_month"] == "2026-07-01"

        resp_get = inv_sm_client.get(
            _invoice_url(inv_sm_app._test_project_id, invoice_id),
            headers=_auth(admin_token),
        )
        assert resp_get.status_code == 200
        assert resp_get.get_json()["service_month"] == "2026-07-01"
