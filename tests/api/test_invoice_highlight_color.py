"""API integration tests for invoice highlight_color (row-highlight feature).

Covers:
- create invoice with a palette color → returned in response
- create invoice with an off-palette color → 400 (schema Literal rejects it)
- create/list/get responses include highlight_color (null when unset)
- PATCH-only highlight_color on any type → set; all other fields unchanged
- PATCH highlight_color=null → cleared
- PATCH off-palette color → 400
- highlight applies to every invoice type (materials_services + labor here)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel


@pytest.fixture(scope="module")
def inv_hc_app():
    """Flask app wired with invoice use-cases for highlight_color tests."""
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

    class InvHcTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InvHcTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="inv_hc_admin", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="inv_hc_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="Highlight Co",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(
            name="Highlight Test Project",
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

        test_app._test_admin_email = "inv_hc_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_hc_client(inv_hc_app):
    return inv_hc_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_hc_client, inv_hc_app):
    return _login(inv_hc_client, inv_hc_app._test_admin_email, inv_hc_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _ms_invoice_body(**overrides):
    body = {
        "type": "materials_services",
        "issue_date": date.today().isoformat(),
        "recipient_name": "Supplier Co",
        "notes": "Original notes",
        "items": [{"description": "Materials", "quantity": 1, "unit_price": 100}],
    }
    body.update(overrides)
    return body


def _create_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


class TestCreateInvoiceHighlightColor:
    def test_create_with_palette_color_is_returned(self, inv_hc_client, inv_hc_app, admin_token):
        resp = inv_hc_client.post(
            _create_url(inv_hc_app._test_project_id),
            json=_ms_invoice_body(highlight_color="green"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["highlight_color"] == "green"

    def test_create_without_color_is_null(self, inv_hc_client, inv_hc_app, admin_token):
        resp = inv_hc_client.post(
            _create_url(inv_hc_app._test_project_id),
            json=_ms_invoice_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["highlight_color"] is None

    def test_create_with_off_palette_color_returns_400(self, inv_hc_client, inv_hc_app, admin_token):
        resp = inv_hc_client.post(
            _create_url(inv_hc_app._test_project_id),
            json=_ms_invoice_body(highlight_color="chartreuse"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)


class TestUpdateInvoiceHighlightColor:
    def _create(self, client, app, token, **overrides):
        resp = client.post(_create_url(app._test_project_id), json=_ms_invoice_body(**overrides), headers=_auth(token))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()

    def test_patch_only_highlight_color_leaves_other_fields_unchanged(self, inv_hc_client, inv_hc_app, admin_token):
        created = self._create(
            inv_hc_client, inv_hc_app, admin_token, recipient_name="Original Recipient", notes="Keep me"
        )
        invoice_id = created["id"]

        resp = inv_hc_client.put(
            _invoice_url(inv_hc_app._test_project_id, invoice_id),
            json={"highlight_color": "red"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["highlight_color"] == "red"
        assert data["recipient_name"] == "Original Recipient"
        assert data["notes"] == "Keep me"
        assert data["items"] == created["items"]
        assert data["type"] == "materials_services"

    def test_patch_highlight_color_null_clears_it(self, inv_hc_client, inv_hc_app, admin_token):
        created = self._create(inv_hc_client, inv_hc_app, admin_token, highlight_color="blue")
        invoice_id = created["id"]
        assert created["highlight_color"] == "blue"

        resp = inv_hc_client.put(
            _invoice_url(inv_hc_app._test_project_id, invoice_id),
            json={"highlight_color": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["highlight_color"] is None

    def test_patch_off_palette_color_returns_400(self, inv_hc_client, inv_hc_app, admin_token):
        created = self._create(inv_hc_client, inv_hc_app, admin_token)
        resp = inv_hc_client.put(
            _invoice_url(inv_hc_app._test_project_id, created["id"]),
            json={"highlight_color": "neon"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_highlight_color_survives_get_and_list(self, inv_hc_client, inv_hc_app, admin_token):
        created = self._create(inv_hc_client, inv_hc_app, admin_token, highlight_color="purple")
        invoice_id = created["id"]

        got = inv_hc_client.get(_invoice_url(inv_hc_app._test_project_id, invoice_id), headers=_auth(admin_token))
        assert got.status_code == 200
        assert got.get_json()["highlight_color"] == "purple"

        listed = inv_hc_client.get(_create_url(inv_hc_app._test_project_id), headers=_auth(admin_token))
        assert listed.status_code == 200
        match = [i for i in listed.get_json()["invoices"] if i["id"] == invoice_id]
        assert match and match[0]["highlight_color"] == "purple"

    def test_highlight_color_on_labor_invoice(self, inv_hc_client, inv_hc_app, admin_token):
        """Highlight applies to every invoice type — not just materials_services."""
        body = {
            "type": "labor",
            "issue_date": date.today().isoformat(),
            "recipient_name": "Worker Payroll",
            "items": [{"description": "Labor", "quantity": 1, "unit_price": 80}],
            "highlight_color": "orange",
        }
        resp = inv_hc_client.post(_create_url(inv_hc_app._test_project_id), json=body, headers=_auth(admin_token))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["highlight_color"] == "orange"
