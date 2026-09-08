"""Write-gate coverage: document/photo/analysis mutation honours `project:update`.

Phase 5 web-review fix. Before this change, rename/delete on a project
document, update/delete on a project photo, and update/delete on a project
analysis were allowed only for platform `*:*`, the uploader, or the project
owner — an assigned company "manager" (who effectively administers the
project via the D1-D8 permission matrix, and holds `project:update` on it)
got a bare 403 despite being able to do everything else on the project. An
assigned "member" (read-only) must still be refused. A D8 deny row on
`project:update` must still win over the matrix grant (H3).

Fixtures build on the shared `invitation_app`/`inv_client` from conftest.py:
the seeded `_test_project_id` project (owned by `admin_user`) is pointed at
a fresh company, and two new users are attached to that company as "manager"
and "member" and assigned to the project (a `user_projects` row — required
for the resolver's `is_assigned` check). Uploads are always performed by
`admin_token` (the project owner), so `wg_manager`/`wg_member` are neither
the uploader nor the owner — the write-gate outcome is attributable solely
to their matrix role.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from PIL import Image
from werkzeug.datastructures import MultiDict

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["access_token"]


def _make_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (40, 40), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wg_app(invitation_app):
    """Attach a fresh company + matrix manager/member users to `_test_project_id`."""
    from app import db

    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        hasher = Argon2PasswordHasher()

        manager_user = UserModel(
            email="wg_manager@invite-test.com", password_hash=hasher.hash(PASSWORD), is_active=True
        )
        member_user = UserModel(email="wg_member@invite-test.com", password_hash=hasher.hash(PASSWORD), is_active=True)
        db.session.add_all([manager_user, member_user])
        db.session.flush()

        company = CompanyModel(
            id=uuid4(),
            legal_name="Write Gate Co",
            address="1 rue Write Gate",
            created_by=UUID(invitation_app._test_admin_user_id),
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        db.session.add_all(
            [
                # The project moves to this company, so its owner (the shared
                # fixture's admin, who performs every upload below) has to
                # administer it here too.
                UserCompanyAccessModel(
                    user_id=UUID(invitation_app._test_admin_user_id),
                    company_id=company.id,
                    role="admin",
                    is_primary=False,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=manager_user.id, company_id=company.id, role="manager", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=member_user.id, company_id=company.id, role="member", is_primary=True, attached_at=now
                ),
            ]
        )

        project_row = db.session.get(ProjectModel, UUID(invitation_app._test_project_id))
        project_row.company_id = company.id

        from sqlalchemy import text

        # role_id is a non-null FK on user_projects (SqlAlchemyProjectMembershipRepository.
        # find_role_id crashes on NULL) — reuse the seeded member_role, which only grants
        # legacy `project:read`, so the write-gate outcome below is attributable to the
        # company-matrix role (resolver), not this placeholder legacy role.
        #
        # admin_user (project owner) also needs an explicit row: on SQLite,
        # ProjectMembershipReaderPort.is_member()'s raw-SQL owner-check UNION branch
        # compares `:uid`/`:pid` string literals against columns without the
        # dialect-normalization SqlAlchemyAuthzReader applies elsewhere, so it never
        # matches (see test_project_analyses_endpoints.py's analyses_app fixture,
        # same workaround) — the analyses CREATE route requires is_member() to pass.
        for uid in (UUID(invitation_app._test_admin_user_id), manager_user.id, member_user.id):
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, role_id, assigned_at) "
                    "VALUES (:uid, :pid, :rid, :at) "
                    "ON CONFLICT (user_id, project_id) DO NOTHING"
                ),
                {
                    "uid": str(uid),
                    "pid": invitation_app._test_project_id,
                    "rid": invitation_app._test_member_role_id,
                    "at": now,
                },
            )
        db.session.commit()

        invitation_app._wg_manager_email = "wg_manager@invite-test.com"
        invitation_app._wg_member_email = "wg_member@invite-test.com"
        invitation_app._wg_manager_user_id = str(manager_user.id)
        invitation_app._wg_company_id = str(company.id)

    return invitation_app


@pytest.fixture
def wg_manager_token(inv_client, wg_app):
    return _login(inv_client, wg_app._wg_manager_email)


@pytest.fixture
def wg_member_token(inv_client, wg_app):
    return _login(inv_client, wg_app._wg_member_email)


@pytest.fixture
def denied_project_update(wg_app, monkeypatch):
    """Monkeypatch authz_reader.grants_for to return a D8 deny row for
    project:update — as if a company admin had denied the manager that
    permission on this project (H3: deny must win over the matrix grant)."""
    from wiring import get_container

    with wg_app.app_context():
        reader = get_container().authz_reader

        def _fake_grants_for(user_id, company_id, project_id):
            return [("project:update", "deny")]

        monkeypatch.setattr(reader, "grants_for", _fake_grants_for)
    yield


def _upload_document(inv_client, admin_token, project_id: str) -> str:
    resp = inv_client.post(
        f"/api/v1/projects/{project_id}/documents",
        data={"file": (io.BytesIO(b"hello world"), "doc.pdf", "application/pdf")},
        content_type="multipart/form-data",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _upload_photo(inv_client, admin_token, project_id: str) -> str:
    resp = inv_client.post(
        f"/api/v1/projects/{project_id}/photos",
        data={"file": (io.BytesIO(_make_jpeg_bytes()), "photo.jpg", "image/jpeg")},
        content_type="multipart/form-data",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _upload_analysis(inv_client, admin_token, project_id: str) -> str:
    data = MultiDict()
    data.add("file", (io.BytesIO(b"<html><body>Test</body></html>"), "test.html", "text/html"))
    data.add("title", "Write Gate Analysis")
    resp = inv_client.post(
        f"/api/v1/projects/{project_id}/analyses",
        data=data,
        content_type="multipart/form-data",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Documents — rename + delete
# ---------------------------------------------------------------------------


class TestDocumentWriteGate:
    def test_manager_can_rename(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/documents/{doc_id}/rename",
            json={"filename": "renamed.pdf"},
            headers=_auth(wg_manager_token),
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["filename"] == "renamed.pdf"

    def test_manager_can_delete(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/documents/{doc_id}", headers=_auth(wg_manager_token))
        assert resp.status_code == 204

    def test_member_cannot_rename(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/documents/{doc_id}/rename",
            json={"filename": "renamed.pdf"},
            headers=_auth(wg_member_token),
        )
        assert resp.status_code == 403

    def test_member_cannot_delete(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/documents/{doc_id}", headers=_auth(wg_member_token))
        assert resp.status_code == 403

    def test_deny_of_project_update_blocks_manager_rename(
        self, inv_client, admin_token, wg_manager_token, wg_app, denied_project_update
    ):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/documents/{doc_id}/rename",
            json={"filename": "renamed.pdf"},
            headers=_auth(wg_manager_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Photos — update + delete
# ---------------------------------------------------------------------------


class TestPhotoWriteGate:
    def test_manager_can_update(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        photo_id = _upload_photo(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/photos/{photo_id}",
            json={"caption": "Manager edit"},
            headers=_auth(wg_manager_token),
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["caption"] == "Manager edit"

    def test_manager_can_delete(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        photo_id = _upload_photo(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/photos/{photo_id}", headers=_auth(wg_manager_token))
        assert resp.status_code == 204

    def test_member_cannot_update(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        photo_id = _upload_photo(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/photos/{photo_id}",
            json={"caption": "Member edit"},
            headers=_auth(wg_member_token),
        )
        assert resp.status_code == 403

    def test_member_cannot_delete(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        photo_id = _upload_photo(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/photos/{photo_id}", headers=_auth(wg_member_token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Analyses — update + delete
# ---------------------------------------------------------------------------


class TestAnalysisWriteGate:
    def test_manager_can_update(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        analysis_id = _upload_analysis(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/analyses/{analysis_id}",
            json={"title": "Manager Renamed"},
            headers=_auth(wg_manager_token),
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["title"] == "Manager Renamed"

    def test_manager_can_delete(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        analysis_id = _upload_analysis(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/analyses/{analysis_id}", headers=_auth(wg_manager_token))
        assert resp.status_code == 204

    def test_member_cannot_update(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        analysis_id = _upload_analysis(inv_client, admin_token, pid)
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/analyses/{analysis_id}",
            json={"title": "Member Renamed"},
            headers=_auth(wg_member_token),
        )
        assert resp.status_code == 403

    def test_member_cannot_delete(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        analysis_id = _upload_analysis(inv_client, admin_token, pid)
        resp = inv_client.delete(f"/api/v1/projects/{pid}/analyses/{analysis_id}", headers=_auth(wg_member_token))
        assert resp.status_code == 403
