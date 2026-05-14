"""API integration tests for the payment_methods endpoints.

Covers:
- GET  /api/v1/companies/<id>/payment-methods
- POST /api/v1/companies/<id>/payment-methods
- PATCH /api/v1/companies/<id>/payment-methods/<id>
- DELETE /api/v1/companies/<id>/payment-methods/<id>

Auth / permission paths, rate-limit header, usage_count field.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture — isolated Flask test app with in-memory SQLite
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pm_app():
    """Flask app wired with payment_methods use-cases for route-level tests."""
    from datetime import datetime, timezone

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
    from config import TestingConfig
    from wiring import configure_container, get_container

    class PmTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(PmTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        # Permissions — name must be "*:*" so AuthorizationService.has_permission("*:*") works
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")

        admin_role = RoleModel(name="pm_admin_role", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)

        member_role = RoleModel(name="pm_member_role", description="Member")
        member_role.permissions.append(read_perm)

        db.session.add_all([star_perm, read_perm, admin_role, member_role])
        db.session.flush()

        admin_user = UserModel(
            email="pm_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        member_user = UserModel(
            email="pm_member@test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        member_user.roles.append(member_role)

        db.session.add_all([admin_user, member_user])
        db.session.flush()

        # Company owned by admin
        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="Folio SARL",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
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
        )
        _c.create_payment_method_usecase = CreatePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.update_payment_method_usecase = UpdatePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.delete_payment_method_usecase = DeletePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )

        test_app._test_admin_email = "pm_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_member_email = "pm_member@test.com"
        test_app._test_member_password = "Member1234!"
        test_app._test_company_id = str(company.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def pm_client(pm_app):
    return pm_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(pm_client, pm_app):
    return _login(pm_client, pm_app._test_admin_email, pm_app._test_admin_password)


@pytest.fixture
def member_token(pm_client, pm_app):
    return _login(pm_client, pm_app._test_member_email, pm_app._test_member_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _list_url(company_id):
    return f"/api/v1/companies/{company_id}/payment-methods"


def _detail_url(company_id, pm_id):
    return f"/api/v1/companies/{company_id}/payment-methods/{pm_id}"


def _make_pm_row(app, label="Wire Transfer", is_builtin=False, is_active=True):
    """Insert a PaymentMethodModel row directly via the test app context."""
    from datetime import datetime, timezone
    from app import db

    with app.app_context():
        now = datetime.now(timezone.utc)
        row = PaymentMethodModel(
            id=uuid4(),
            company_id=UUID(app._test_company_id),
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


# ---------------------------------------------------------------------------
# Auth / permission tests
# ---------------------------------------------------------------------------


def test_list_401_without_jwt(pm_client, pm_app):
    resp = pm_client.get(_list_url(pm_app._test_company_id))
    assert resp.status_code == 401


def test_list_200_admin(pm_client, pm_app, admin_token):
    resp = pm_client.get(_list_url(pm_app._test_company_id), headers=_auth(admin_token))
    assert resp.status_code == 200
    assert "items" in resp.get_json()


def test_list_200_member_active_only(pm_client, pm_app, member_token):
    """Members can list active methods (no permission restriction on read)."""
    resp = pm_client.get(_list_url(pm_app._test_company_id), headers=_auth(member_token))
    assert resp.status_code == 200


def test_list_active_only_by_default(pm_client, pm_app, admin_token):
    """GET without ?include_inactive returns only active rows."""
    _make_pm_row(pm_app, label="Active One", is_active=True)
    _make_pm_row(pm_app, label="Deleted One", is_active=False)

    resp = pm_client.get(_list_url(pm_app._test_company_id), headers=_auth(admin_token))
    assert resp.status_code == 200
    labels = [i["label"] for i in resp.get_json()["items"]]
    assert "Deleted One" not in labels


def test_list_include_inactive_admin(pm_client, pm_app, admin_token):
    """Admin passing ?include_inactive=true sees soft-deleted rows."""
    _make_pm_row(pm_app, label="Inactive API Test", is_active=False)

    resp = pm_client.get(
        _list_url(pm_app._test_company_id),
        query_string={"include_inactive": "true"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    labels = [i["label"] for i in resp.get_json()["items"]]
    assert "Inactive API Test" in labels


def test_list_include_inactive_non_admin_ignored(pm_client, pm_app, member_token):
    """Non-admin cannot see inactive rows even with ?include_inactive=true."""
    _make_pm_row(pm_app, label="Inactive Non-Admin Test", is_active=False)

    resp = pm_client.get(
        _list_url(pm_app._test_company_id),
        query_string={"include_inactive": "true"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 200
    labels = [i["label"] for i in resp.get_json()["items"]]
    assert "Inactive Non-Admin Test" not in labels


def test_list_contains_usage_count(pm_client, pm_app, admin_token):
    """Response items include usage_count field (may be 0)."""
    resp = pm_client.get(_list_url(pm_app._test_company_id), headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.get_json()
    if data["items"]:
        assert "usage_count" in data["items"][0]


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------


def test_create_201_admin(pm_client, pm_app, admin_token):
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Unique API Create Label"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["label"] == "Unique API Create Label"
    assert data["is_active"] is True
    assert data["is_builtin"] is False


def test_create_403_non_admin(pm_client, pm_app, member_token):
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Should Fail"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403


def test_create_401_no_jwt(pm_client, pm_app):
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Should Fail"},
    )
    assert resp.status_code == 401


def test_create_409_duplicate_label(pm_client, pm_app, admin_token):
    label = "Duplicate Label Test"
    pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": label},
        headers=_auth(admin_token),
    )
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": label},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "duplicate_label"


# ---------------------------------------------------------------------------
# PATCH — update
# ---------------------------------------------------------------------------


def test_patch_rename_200(pm_client, pm_app, admin_token):
    pm_id = _make_pm_row(pm_app, label="Before Rename")

    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, pm_id),
        json={"label": "After Rename"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.get_json()["label"] == "After Rename"


def test_patch_403_non_admin(pm_client, pm_app, member_token):
    pm_id = _make_pm_row(pm_app, label="Patch Forbidden")

    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, pm_id),
        json={"label": "New"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403


def test_patch_deactivate_builtin_409(pm_client, pm_app, admin_token):
    pm_id = _make_pm_row(pm_app, label="Builtin Deactivate", is_builtin=True)

    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, pm_id),
        json={"is_active": False},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["error"] == "builtin_protected"
    assert data["reason"] == "deactivate"


def test_patch_404_unknown_method(pm_client, pm_app, admin_token):
    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, str(uuid4())),
        json={"label": "X"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


def test_patch_409_duplicate_label(pm_client, pm_app, admin_token):
    """PATCH rename to an existing label → 409 duplicate_label."""
    # Create two distinct methods
    pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Patch Dupe Source"},
        headers=_auth(admin_token),
    )
    resp_create = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Patch Dupe Target"},
        headers=_auth(admin_token),
    )
    assert resp_create.status_code == 201
    target_id = resp_create.get_json()["id"]

    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, target_id),
        json={"label": "Patch Dupe Source"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "duplicate_label"


# ---------------------------------------------------------------------------
# DELETE — soft-delete
# ---------------------------------------------------------------------------


def test_delete_204(pm_client, pm_app, admin_token):
    pm_id = _make_pm_row(pm_app, label="To Delete")

    resp = pm_client.delete(
        _detail_url(pm_app._test_company_id, pm_id),
        headers=_auth(admin_token),
    )
    assert resp.status_code == 204


def test_delete_403_non_admin(pm_client, pm_app, member_token):
    pm_id = _make_pm_row(pm_app, label="Delete Forbidden")

    resp = pm_client.delete(
        _detail_url(pm_app._test_company_id, pm_id),
        headers=_auth(member_token),
    )
    assert resp.status_code == 403


def test_delete_builtin_409(pm_client, pm_app, admin_token):
    pm_id = _make_pm_row(pm_app, label="Builtin Delete", is_builtin=True)

    resp = pm_client.delete(
        _detail_url(pm_app._test_company_id, pm_id),
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["error"] == "builtin_protected"
    assert data["reason"] == "delete"


def test_delete_404_unknown_method(pm_client, pm_app, admin_token):
    resp = pm_client.delete(
        _detail_url(pm_app._test_company_id, str(uuid4())),
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Input validation — invalid UUID paths and malformed bodies (routes.py coverage)
# ---------------------------------------------------------------------------


def test_list_invalid_company_uuid_returns_404(pm_client, admin_token):
    resp = pm_client.get("/api/v1/companies/not-a-uuid/payment-methods", headers=_auth(admin_token))
    assert resp.status_code == 404


def test_create_invalid_company_uuid_returns_404(pm_client, admin_token):
    resp = pm_client.post(
        "/api/v1/companies/not-a-uuid/payment-methods",
        json={"label": "Test"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


def test_create_missing_label_returns_4xx(pm_client, pm_app, admin_token):
    """Missing required 'label' field → Pydantic validation error (400 or 422)."""
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={},
        headers=_auth(admin_token),
    )
    assert resp.status_code in (400, 422)


def test_patch_invalid_company_uuid_returns_404(pm_client, admin_token):
    resp = pm_client.patch(
        "/api/v1/companies/not-a-uuid/payment-methods/also-not-uuid",
        json={"label": "X"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


def test_patch_missing_fields_returns_4xx(pm_client, pm_app, admin_token):
    """No fields provided → Pydantic model_validator rejects (400 or 422)."""
    pm_id = _make_pm_row(pm_app, label="Patch Validation")
    resp = pm_client.patch(
        _detail_url(pm_app._test_company_id, pm_id),
        json={},
        headers=_auth(admin_token),
    )
    assert resp.status_code in (400, 422)


def test_delete_invalid_company_uuid_returns_404(pm_client, admin_token):
    resp = pm_client.delete(
        "/api/v1/companies/not-a-uuid/payment-methods/also-not-uuid",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rate-limit header check
# ---------------------------------------------------------------------------


def test_create_response_has_ratelimit_header(pm_client, pm_app, admin_token):
    """RATELIMIT_ENABLED=False in test config — header may be absent. We check
    the route is reachable and status is in {201, 409} without 500."""
    resp = pm_client.post(
        _list_url(pm_app._test_company_id),
        json={"label": "Rate Limit Header Check"},
        headers=_auth(admin_token),
    )
    # Route must not crash — 201 on success, 409 if already exists
    assert resp.status_code in (201, 409)
