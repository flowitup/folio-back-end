"""API smoke tests for GET /projects/<id>/labor-export and worker variant.

Covers:
- 200 happy paths: xlsx magic bytes, pdf magic bytes
- Response headers: Content-Disposition attachment, Cache-Control no-store, X-Content-Type-Options nosniff
- Filename uses slugified project name
- Filename falls back to project ID prefix for non-ASCII-only project names
- 422 validation: from > to, span > 24 months, unknown format, malformed from
- 403 when user lacks project:read
- 404 when project does not exist
- TestWorkerLaborExportEndpoint: single-worker route (14 cases)
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    UserModel,
    ProjectModel,
    RoleModel,
    PermissionModel,
    WorkerModel,
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

        # Role with project:read only — *:* omitted so require_permission("project:read") is exercised
        admin_role = RoleModel(name="export_admin", description="Export Admin")
        admin_role.permissions.append(read_perm)

        # Role without project:read
        noperm_role = RoleModel(name="no_read_role", description="No Read")

        db.session.add_all([read_perm, admin_role, noperm_role])
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
    """Non-existent project UUID → 404.

    @require_project_access() intercepts before the route body, so the error key
    is "NotFound" (from the decorator's _not_found helper).
    """
    missing_id = str(uuid4())
    url = _export_url(missing_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404
    data = resp.get_json()
    # Decorator returns {"error": "NotFound"} — route-level handler is no longer reached.
    assert data["error"] in ("NotFound", "project_not_found")


# ---------------------------------------------------------------------------
# Content-Disposition filename pattern
# ---------------------------------------------------------------------------


def test_export_filename_matches_slug_and_range_pattern(export_client, export_app, admin_token):
    """Content-Disposition filename = labor-{slug}-{from}-to-{to}.xlsx."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-03", "to": "2026-05", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    # Slug for "Labor API Test Project" → "labor-api-test-project"
    assert "labor-labor-api-test-project-2026-03-to-2026-05.xlsx" in cd


def test_export_pdf_filename_matches_slug_and_range_pattern(export_client, export_app, admin_token):
    """Content-Disposition filename = labor-{slug}-{from}-to-{to}.pdf for pdf format."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-02", "format": "pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "labor-api-test-project" in cd
    assert "2026-01-to-2026-02" in cd
    assert ".pdf" in cd


# ---------------------------------------------------------------------------
# Cache-Control and X-Content-Type-Options (must-revalidate + nosniff)
# ---------------------------------------------------------------------------


def test_export_cache_control_includes_must_revalidate(export_client, export_app, admin_token):
    """Cache-Control must include must-revalidate as well as no-store."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc
    assert "must-revalidate" in cc


def test_export_x_content_type_options_exact_value(export_client, export_app, admin_token):
    """X-Content-Type-Options header value is exactly 'nosniff' (case-sensitive check)."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# 422 error envelope JSON-serializability (regression — ctx-stripping fix)
# ---------------------------------------------------------------------------


def test_export_422_envelope_is_json_serializable(export_client, export_app, admin_token):
    """422 response body is valid JSON with 'error', 'details', 'message' keys.

    Regression guard: exc.errors() ctx dict may contain non-serializable objects
    (e.g. ValueError instances). The route strips ctx to safe scalar fields only.
    """
    import json

    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-06", "to": "2026-01", "format": "xlsx"},  # from > to
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422

    # Must be decodable JSON (no UnicodeDecodeError, no internal server error)
    raw = resp.get_data(as_text=True)
    parsed = json.loads(raw)  # raises if not valid JSON

    assert "error" in parsed
    assert parsed["error"] == "validation_error"
    assert "details" in parsed
    assert isinstance(parsed["details"], list)
    assert "message" in parsed
    assert isinstance(parsed["message"], str)

    # Each detail entry must only have JSON-safe scalar values
    for entry in parsed["details"]:
        # Re-serialise to confirm no hidden non-serializable objects
        json.dumps(entry)  # raises TypeError if not serializable


def test_export_422_details_list_has_loc_msg_type(export_client, export_app, admin_token):
    """Each entry in 422 'details' list has 'loc', 'msg', 'type' keys."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-1", "to": "2026-01", "format": "xlsx"},  # malformed from
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert len(data["details"]) > 0
    for entry in data["details"]:
        assert "loc" in entry
        assert "msg" in entry
        assert "type" in entry


# ---------------------------------------------------------------------------
# 401 — no auth token
# ---------------------------------------------------------------------------


def test_export_401_when_no_auth_token(export_client, export_app):
    """Request without Authorization header → 401 (JWT required)."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Empty range — happy path (no labor entries → valid file bytes)
# ---------------------------------------------------------------------------


def test_export_empty_range_xlsx_200_with_valid_bytes(export_client, export_app, admin_token):
    """Month range with no seeded entries still returns 200 with valid xlsx magic bytes."""
    url = _export_url(export_app._test_project_id)
    # Use a far-future month unlikely to have seeded data
    resp = export_client.get(
        url,
        query_string={"from": "2099-01", "to": "2099-01", "format": "xlsx"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.data[:4] == b"PK\x03\x04"


def test_export_empty_range_pdf_200_with_valid_bytes(export_client, export_app, admin_token):
    """Month range with no seeded entries still returns 200 with valid pdf magic bytes."""
    url = _export_url(export_app._test_project_id)
    resp = export_client.get(
        url,
        query_string={"from": "2099-01", "to": "2099-01", "format": "pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.content_type == "application/pdf"
    assert resp.data[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# TestWorkerLaborExportEndpoint
# GET /api/v1/projects/<project_id>/workers/<worker_id>/labor-export
# ---------------------------------------------------------------------------


def _worker_export_url(project_id: str, worker_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers/{worker_id}/labor-export"


@pytest.fixture(scope="module")
def worker_export_app():
    """Flask app seeded with a project, two workers, and a no-perm user."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from config import TestingConfig
    from wiring import configure_container

    class WorkerExportTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(WorkerExportTestConfig)

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
        # *:* omitted — require_permission("project:read") must actually match
        admin_role = RoleModel(name="wexport_admin", description="Worker Export Admin")
        admin_role.permissions.append(read_perm)
        noperm_role = RoleModel(name="wexport_noperm", description="No Perm")

        db.session.add_all([read_perm, admin_role, noperm_role])
        db.session.flush()

        # Users
        admin_user = UserModel(
            email="wexportadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)

        noperm_user = UserModel(
            email="wexportnoperm@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        noperm_user.roles.append(noperm_role)
        db.session.add(noperm_user)
        db.session.flush()

        # Projects
        project = ProjectModel(name="Worker Export Project", owner_id=admin_user.id)
        db.session.add(project)

        other_project = ProjectModel(name="Other Project", owner_id=admin_user.id)
        db.session.add(other_project)
        db.session.flush()

        # Workers — use UUID objects for SQLite compatibility
        worker = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            name="Antoine Dupont",
            daily_rate=Decimal("200.00"),
        )
        db.session.add(worker)

        # Worker that belongs to a DIFFERENT project (for cross-project 404 test)
        other_project_worker = WorkerModel(
            id=uuid4(),
            project_id=other_project.id,
            name="Marc Leblanc",
            daily_rate=Decimal("250.00"),
        )
        db.session.add(other_project_worker)

        # Inactive worker in the same project (for M-5 block test)
        inactive_worker = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            name="Sophie Inactive",
            daily_rate=Decimal("180.00"),
            is_active=False,
        )
        db.session.add(inactive_worker)
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

        test_app._test_admin_email = "wexportadmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_noperm_email = "wexportnoperm@test.com"
        test_app._test_noperm_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_worker_id = str(worker.id)
        test_app._test_worker_name = "Antoine Dupont"
        test_app._test_other_project_worker_id = str(other_project_worker.id)
        test_app._test_inactive_worker_id = str(inactive_worker.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def worker_export_client(worker_export_app):
    return worker_export_app.test_client()


@pytest.fixture
def we_admin_token(worker_export_client, worker_export_app):
    return _login(
        worker_export_client,
        worker_export_app._test_admin_email,
        worker_export_app._test_admin_password,
    )


@pytest.fixture
def we_noperm_token(worker_export_client, worker_export_app):
    return _login(
        worker_export_client,
        worker_export_app._test_noperm_email,
        worker_export_app._test_noperm_password,
    )


class TestWorkerLaborExportEndpoint:
    """14 cases for GET /projects/<pid>/workers/<wid>/labor-export."""

    # --- Case 1: 200 xlsx magic bytes + headers ---

    def test_200_xlsx_magic_bytes_and_headers(self, worker_export_client, worker_export_app, we_admin_token):
        """200 xlsx: PK magic, CD attachment, CC no-store, XCTO nosniff, filename has worker slug."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.data[:4] == b"PK\x03\x04", "Expected xlsx ZIP magic bytes"
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd
        assert "labor-" in cd
        # Worker slug for "Antoine Dupont" → "antoine-dupont"
        assert "antoine-dupont" in cd, f"Worker slug missing from CD: {cd}"
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    # --- Case 2: 200 pdf magic bytes ---

    def test_200_pdf_magic_bytes(self, worker_export_client, worker_export_app, we_admin_token):
        """200 pdf: %PDF- magic, filename ends .pdf."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "pdf"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.data[:5] == b"%PDF-", "Expected PDF magic bytes"
        cd = resp.headers.get("Content-Disposition", "")
        assert ".pdf" in cd

    # --- Case 3: 422 from > to ---

    def test_422_from_after_to(self, worker_export_client, worker_export_app, we_admin_token):
        """from > to → 422 validation_error."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-06", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "validation_error"

    # --- Case 4: 422 span > 24 months ---

    def test_422_span_exceeds_24_months(self, worker_export_client, worker_export_app, we_admin_token):
        """25-month span → 422 validation_error."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2024-01", "to": "2026-02", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "validation_error"

    # --- Case 5: 422 malformed format ---

    def test_422_malformed_format(self, worker_export_client, worker_export_app, we_admin_token):
        """format=csv → 422 validation_error."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "csv"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "validation_error"

    # --- Case 6: 422 malformed YYYY-MM ---

    def test_422_malformed_from_date(self, worker_export_client, worker_export_app, we_admin_token):
        """from=2026-1 (no zero-padding) → 422 validation_error."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-1", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "validation_error"

    # --- Case 7: 422 invalid worker_id (not a UUID) ---

    def test_422_invalid_worker_id_not_uuid(self, worker_export_client, worker_export_app, we_admin_token):
        """Non-UUID worker_id path param → 422 invalid_worker_id."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            "not-a-uuid",
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "invalid_worker_id"

    # --- Case 8: 404 project not found ---

    def test_404_project_not_found(self, worker_export_client, worker_export_app, we_admin_token):
        """Non-existent project UUID → 404.

        @require_project_access() intercepts before the route body, returning {"error": "NotFound"}.
        """
        url = _worker_export_url(str(uuid4()), worker_export_app._test_worker_id)
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 404
        # Decorator returns {"error": "NotFound"} — route-level handler is no longer reached.
        assert resp.get_json()["error"] in ("NotFound", "project_not_found")

    # --- Case 9: 404 worker not found (legitimate UUID, no row) ---

    def test_404_worker_not_found_unknown_uuid(self, worker_export_client, worker_export_app, we_admin_token):
        """Valid UUID that doesn't match any worker → 404 worker_not_found."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            str(uuid4()),
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "worker_not_found"

    # --- Case 10: 404 worker exists but in different project ---

    def test_404_worker_in_different_project(self, worker_export_client, worker_export_app, we_admin_token):
        """Worker exists but belongs to a different project → 404 worker_not_found."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_other_project_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "worker_not_found"

    # --- Case 11: 401 missing auth ---

    def test_401_no_auth_header(self, worker_export_client, worker_export_app):
        """No Authorization header → 401."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
        )
        assert resp.status_code == 401

    # --- Case 12: 403 lacks project:read ---

    def test_403_lacks_project_read(self, worker_export_client, worker_export_app, we_noperm_token):
        """User without project:read → 403."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_noperm_token),
        )
        assert resp.status_code == 403

    # --- Case 13: 429 rate-limited (6th call within 1 min) ---

    # NOTE: 429 rate-limit test is skipped — RATELIMIT_ENABLED=False in TestingConfig.
    # The global `limiter` singleton shares memory://  storage across Flask app instances
    # created within the same process, making isolation between module-scoped fixtures
    # and a dedicated RL-enabled app unreliable.
    # The rate-limit mechanism (flask-limiter, @limiter.limit("5 per minute")) is
    # verified by the existing test in tests/test_auth_endpoints.py::TestRateLimit.
    @pytest.mark.skip(
        reason=(
            "Rate-limit test skipped: RATELIMIT_ENABLED=False in TestingConfig. "
            "Global limiter singleton shares memory:// storage across app instances "
            "in the same process, causing bleed between module-scoped fixtures. "
            "Mechanism verified by test_auth_endpoints.py::TestRateLimit."
        )
    )
    def test_429_rate_limited_on_sixth_call(self):
        """6th call within 1 minute → 429 (rate limit: 5/min)."""
        pass  # See skip reason above

    # --- Case 14: 200 empty range — file has empty-state message; no crash ---

    # --- Case 13.5 (M-5): 404 inactive worker blocked at backend ---

    def test_404_inactive_worker_blocked(self, worker_export_client, worker_export_app, we_admin_token):
        """Inactive worker → 404 with error code 'worker_inactive' (hard backend block).

        The FE hides the export button for inactive workers, but a direct API call
        must also be refused so the protection is not purely UI-gated.
        """
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_inactive_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 404, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["error"] == "worker_inactive", f"Expected 'worker_inactive', got: {body}"

    def test_200_empty_range_xlsx_valid_bytes(self, worker_export_client, worker_export_app, we_admin_token):
        """Empty date range (no entries) → 200 with valid xlsx magic bytes, no crash."""
        url = _worker_export_url(
            worker_export_app._test_project_id,
            worker_export_app._test_worker_id,
        )
        resp = worker_export_client.get(
            url,
            query_string={"from": "2099-01", "to": "2099-01", "format": "xlsx"},
            headers=_auth(we_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.data[:4] == b"PK\x03\x04", "Expected xlsx magic bytes for empty range"
        # Verify the workbook opens and contains the empty-state message
        import openpyxl
        from io import BytesIO

        wb = openpyxl.load_workbook(BytesIO(resp.data), data_only=True)
        ws = wb.active
        all_values = [ws.cell(row=r, column=1).value for r in range(1, 20)]
        no_entries = any(v and "No labor entries in range" in str(v) for v in all_values)
        assert no_entries, f"Empty-state message missing. Col A: {all_values}"

    # --- Case 15: non-ASCII project + worker name → filename uses UUID prefix fallback ---

    def test_filename_fallback_for_cjk_project_and_worker_name(
        self, cjk_worker_export_client, cjk_worker_export_app, cjk_admin_token
    ):
        """CJK/emoji project name and worker name → filename uses UUID-prefix slug for both.

        Asserts that slugify falls back to the first 8 hex chars of each UUID and that
        no question marks or replacement characters appear in the filename.
        Expected pattern: labor-{8-char project prefix}-{8-char worker prefix}-{from}-to-{to}.{ext}
        """
        url = _worker_export_url(
            cjk_worker_export_app._test_project_id,
            cjk_worker_export_app._test_worker_id,
        )
        resp = cjk_worker_export_client.get(
            url,
            query_string={"from": "2026-01", "to": "2026-01", "format": "xlsx"},
            headers=_auth(cjk_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

        cd = resp.headers.get("Content-Disposition", "")
        # Extract filename from Content-Disposition
        filename = ""
        for part in cd.split(";"):
            part = part.strip()
            if part.startswith("filename="):
                filename = part[len("filename=") :].strip('"')
                break

        # Both project and worker UUIDs have no hyphens in slug fallback
        project_id = cjk_worker_export_app._test_project_id
        worker_id = cjk_worker_export_app._test_worker_id
        project_prefix = project_id.replace("-", "")[:8]
        worker_prefix = worker_id.replace("-", "")[:8]

        assert project_prefix in filename, f"Expected project UUID prefix '{project_prefix}' in filename '{filename}'"
        assert worker_prefix in filename, f"Expected worker UUID prefix '{worker_prefix}' in filename '{filename}'"
        assert "?" not in filename, f"Replacement/question-mark char in filename: {filename}"
        assert "�" not in filename, f"Unicode replacement char in filename: {filename}"
        assert filename.endswith(".xlsx"), f"Expected .xlsx extension in filename: {filename}"


# ---------------------------------------------------------------------------
# Fixtures for Case 15 — CJK project + worker names
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cjk_worker_export_app():
    """Flask app seeded with a CJK-named project and CJK-named worker for slug-fallback test."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from config import TestingConfig
    from wiring import configure_container

    class CJKWorkerExportTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CJKWorkerExportTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        # *:* omitted — require_permission("project:read") must actually match
        admin_role = RoleModel(name="cjk_admin_role", description="CJK Admin")
        admin_role.permissions.append(read_perm)

        db.session.add_all([read_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="cjkadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        # Project whose name is pure CJK + emoji → slug falls back to UUID prefix
        cjk_project = ProjectModel(name="工地🏗️", owner_id=admin_user.id)
        db.session.add(cjk_project)
        db.session.flush()

        # Worker whose name is pure CJK → slug falls back to UUID prefix
        cjk_worker = WorkerModel(
            id=uuid4(),
            project_id=cjk_project.id,
            name="工人",
            daily_rate=Decimal("180.00"),
        )
        db.session.add(cjk_worker)
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

        test_app._test_project_id = str(cjk_project.id)
        test_app._test_worker_id = str(cjk_worker.id)
        test_app._test_admin_email = "cjkadmin@test.com"
        test_app._test_admin_password = "Admin1234!"

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def cjk_worker_export_client(cjk_worker_export_app):
    return cjk_worker_export_app.test_client()


@pytest.fixture
def cjk_admin_token(cjk_worker_export_client, cjk_worker_export_app):
    return _login(
        cjk_worker_export_client,
        cjk_worker_export_app._test_admin_email,
        cjk_worker_export_app._test_admin_password,
    )
