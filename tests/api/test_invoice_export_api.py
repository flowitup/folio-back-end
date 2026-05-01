"""API smoke tests for GET /api/v1/projects/<id>/invoices-export.

Mirrors tests/api/test_labor_export_endpoint.py fixture style.

Covers:
- 401 — no auth token
- 422 — missing format param
- 422 — invalid format value
- 422 — range exceeds 24 months
- 422 — from > to
- 404 — unknown project
- 200 xlsx smoke — magic bytes PK, content-type, Content-Disposition
- 200 pdf smoke — magic bytes %PDF-, content-type
- type filter in filename — ?type=labor → filename has '-labor'
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    PermissionModel,
    ProjectModel,
    RoleModel,
    UserModel,
)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inv_export_app():
    """Flask app with in-memory DB + full invoice + export container for route tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from config import TestingConfig
    from wiring import configure_container

    class InvExportTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InvExportTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)

        # Permissions
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        # Role with project:read
        admin_role = RoleModel(name="inv_export_admin", description="Inv Export Admin")
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(star_perm)

        # Role without project:read
        noperm_role = RoleModel(name="inv_export_noperm", description="No Read")

        db.session.add_all([read_perm, star_perm, admin_role, noperm_role])
        db.session.flush()

        # Admin user
        admin_user = UserModel(
            email="invexportadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        # Unprivileged user
        noperm_user = UserModel(
            email="invexportnoperm@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        noperm_user.roles.append(noperm_role)
        db.session.add(noperm_user)
        db.session.flush()

        # Project
        project = ProjectModel(
            name="Invoice Export Test Project",
            owner_id=admin_user.id,
        )
        db.session.add(project)
        db.session.commit()

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        test_app._test_admin_email = "invexportadmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_export_client(inv_export_app):
    return inv_export_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_export_client, inv_export_app):
    return _login(inv_export_client, inv_export_app._test_admin_email, inv_export_app._test_admin_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _export_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/invoices-export"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unauth_returns_401(inv_export_client, inv_export_app):
    """No Authorization header → 401."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
    )
    assert resp.status_code == 401


def test_missing_format_returns_422(inv_export_client, inv_export_app, admin_token):
    """Missing 'format' param → 422 validation_error."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "validation_error"


def test_invalid_format_returns_422(inv_export_client, inv_export_app, admin_token):
    """format=csv → 422 validation_error."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "csv"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "validation_error"


def test_range_too_large_returns_422(inv_export_client, inv_export_app, admin_token):
    """25-month range (from=2024-01, to=2026-02) → 422."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2024-01", "to": "2026-02", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "validation_error"


def test_from_after_to_returns_422(inv_export_client, inv_export_app, admin_token):
    """from > to → 422 validation_error."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-06", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "validation_error"


def test_unknown_project_returns_404(inv_export_client, inv_export_app, admin_token):
    """Non-existent project UUID → 404.

    @require_project_access() intercepts before route body and returns {"error": "NotFound"}.
    """
    url = _export_url(str(uuid4()))
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"] in ("NotFound", "project_not_found")


def test_xlsx_smoke(inv_export_client, inv_export_app, admin_token):
    """200 xlsx: PK magic bytes, correct content-type, attachment Content-Disposition."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.data[:4] == b"PK\x03\x04", "Expected xlsx ZIP magic bytes"

    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert "invoices-" in cd
    assert ".xlsx" in cd


def test_pdf_smoke(inv_export_client, inv_export_app, admin_token):
    """200 pdf: %PDF- magic bytes, correct content-type."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.content_type == "application/pdf"
    assert resp.data[:5] == b"%PDF-", "Expected PDF magic bytes"


def test_type_filter_in_filename(inv_export_client, inv_export_app, admin_token):
    """?type=labor → Content-Disposition filename contains '-labor'."""
    url = _export_url(inv_export_app._test_project_id)
    resp = inv_export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx", "type": "labor"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cd = resp.headers.get("Content-Disposition", "")
    assert "-labor" in cd, f"Expected '-labor' suffix in Content-Disposition; got: {cd}"
    assert ".xlsx" in cd
