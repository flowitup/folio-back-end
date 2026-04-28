"""API smoke tests for GET /projects/<id>/labor-export.

Covers:
- 200 happy paths: xlsx magic bytes, pdf magic bytes
- Response headers: Content-Disposition attachment, Cache-Control no-store, X-Content-Type-Options nosniff
- Filename uses slugified project name
- Filename falls back to project ID prefix for non-ASCII-only project names
- 422 validation: from > to, span > 24 months, unknown format, malformed from
- 403 when user lacks project:read
- 404 when project does not exist
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    UserModel,
    ProjectModel,
    RoleModel,
    PermissionModel,
)
from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def export_app():
    """Flask app with in-memory DB + full labor + export container for route tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from config import TestingConfig
    from wiring import configure_container

    class ExportTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(ExportTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        # Permissions
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        # Role with project:read
        admin_role = RoleModel(name="export_admin", description="Export Admin")
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(star_perm)

        # Role without project:read
        noperm_role = RoleModel(name="no_read_role", description="No Read")

        db.session.add_all([read_perm, star_perm, admin_role, noperm_role])
        db.session.flush()

        # Admin user (has project:read)
        admin_user = UserModel(
            email="exportadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        # Unprivileged user (lacks project:read)
        noperm_user = UserModel(
            email="noperm@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        noperm_user.roles.append(noperm_role)
        db.session.add(noperm_user)
        db.session.flush()

        # Project with ASCII name → slug "labor-api-test-project"
        project = ProjectModel(
            name="Labor API Test Project",
            owner_id=admin_user.id,
        )
        db.session.add(project)

        # Project with non-ASCII name → slug falls back to ID prefix
        nonascii_project = ProjectModel(
            name="🏗️工地",
            owner_id=admin_user.id,
        )
        db.session.add(nonascii_project)

        db.session.commit()

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=token_issuer,
            session_manager=FlaskSessionManager(),
            worker_repository=worker_repo,
            labor_entry_repository=entry_repo,
        )

        test_app._test_admin_email = "exportadmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_noperm_email = "noperm@test.com"
        test_app._test_noperm_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_nonascii_project_id = str(nonascii_project.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def export_client(export_app):
    return export_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(export_client, export_app):
    return _login(export_client, export_app._test_admin_email, export_app._test_admin_password)


@pytest.fixture
def noperm_token(export_client, export_app):
    return _login(export_client, export_app._test_noperm_email, export_app._test_noperm_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _export_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-export"


# ---------------------------------------------------------------------------
# Happy path — magic bytes
# ---------------------------------------------------------------------------


def test_export_xlsx_200_returns_pk_magic(export_client, export_app, admin_token):
    """xlsx bytes start with PK\\x03\\x04 (ZIP local file header)."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.data[:4] == b"PK\x03\x04", "Expected xlsx (ZIP) magic bytes"


def test_export_pdf_200_returns_pdf_magic(export_client, export_app, admin_token):
    """pdf bytes start with %PDF-."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.content_type == "application/pdf"
    assert resp.data[:5] == b"%PDF-", "Expected PDF magic bytes"


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


def test_export_sets_content_disposition_attachment(export_client, export_app, admin_token):
    """Content-Disposition must be attachment; filename starts with 'labor-'."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert "labor-" in cd


def test_export_sets_cache_control_no_store(export_client, export_app, admin_token):
    """Cache-Control must include no-store."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc


def test_export_sets_x_content_type_options_nosniff(export_client, export_app, admin_token):
    """X-Content-Type-Options must be nosniff."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# Filename slug
# ---------------------------------------------------------------------------


def test_export_filename_uses_slugified_project_name(export_client, export_app, admin_token):
    """Filename uses slugified project name for ASCII project names."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-03", "to": "2026-05", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    # "Labor API Test Project" → slug "labor-api-test-project"
    assert "labor-api-test-project" in cd
    assert "2026-03" in cd
    assert "2026-05" in cd


def test_export_filename_falls_back_to_project_id_for_non_ascii_only_name(export_client, export_app, admin_token):
    """For pure non-ASCII names (emoji/CJK), filename falls back to first 8 chars of project ID."""
    project_id = export_app._test_nonascii_project_id
    url = _export_url(project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    # Slug = first 8 chars of project UUID (no hyphens in slugify output, just the chars)
    id_prefix = project_id.replace("-", "")[:8]
    assert id_prefix in cd or project_id[:8] in cd, f"Expected ID prefix in Content-Disposition; got: {cd}"


# ---------------------------------------------------------------------------
# 422 validation errors
# ---------------------------------------------------------------------------


def test_export_422_when_from_after_to(export_client, export_app, admin_token):
    """from > to → 422."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-06", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "validation_error"


def test_export_422_when_span_exceeds_24_months(export_client, export_app, admin_token):
    """25-month range → 422."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2024-01", "to": "2026-02", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "validation_error"


def test_export_422_when_format_unknown(export_client, export_app, admin_token):
    """format=csv → 422."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "csv"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "validation_error"


def test_export_422_when_from_malformed(export_client, export_app, admin_token):
    """from=2026-1 (no zero-padding) → 422."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-1", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "validation_error"


# ---------------------------------------------------------------------------
# 403 — missing permission
# ---------------------------------------------------------------------------


def test_export_403_when_user_lacks_project_read(export_client, export_app, noperm_token):
    """User without project:read gets 403."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(noperm_token),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 404 — project not found
# ---------------------------------------------------------------------------


def test_export_404_when_project_not_found(export_client, export_app, admin_token):
    """Non-existent project UUID → 404."""
    missing_id = str(uuid4())
    url = _export_url(missing_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"] == "project_not_found"
