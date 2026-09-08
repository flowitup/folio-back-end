"""Write-gate coverage: the site journal honours `project:update` (D9).

Phase 5 web-review fix. Before this change, rename/delete on a project
document, update/delete on a project photo, and update/delete on a project
analysis were allowed only for platform `*:*`, the uploader, or the project
owner — an assigned company "manager" (who effectively administers the
project via the D1-D8 permission matrix, and holds `project:update` on it)
got a bare 403 despite being able to do everything else on the project. An
assigned "member" (read-only) must still be refused. A D8 deny row on
`project:update` must still win over the matrix grant (H3).

D9 extends that rule to the whole site journal: documents are off-limits to a
member (every route, reads included, needs `project:update`), while notes,
analyses and photos stay readable at `project:read` and writable only with
`project:update`. A member holding a D8 **grant** of `project:update` gets the
documents module back — there is no documents-specific permission name.

Fixtures build on the shared `invitation_app`/`inv_client` from conftest.py:
the seeded `_test_project_id` project (owned by `admin_user`) is pointed at
a fresh company, and three new users are attached to that company as
"manager", "member", and "member + D8 grant of project:update", each assigned
to the project (a `user_projects` row — required
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
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
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
        # Same company role as member_user; the D8 grant row below is the only
        # difference, so any 200 it earns is attributable to that row alone.
        grantee_user = UserModel(
            email="wg_grantee@invite-test.com", password_hash=hasher.hash(PASSWORD), is_active=True
        )
        db.session.add_all([manager_user, member_user, grantee_user])
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
                UserCompanyAccessModel(
                    user_id=grantee_user.id, company_id=company.id, role="member", is_primary=True, attached_at=now
                ),
                # D8: a company admin opens the documents module to one member
                # on one project by granting `project:update` there (there is no
                # documents-specific permission name to grant).
                CompanyMemberGrantModel(
                    id=uuid4(),
                    company_id=company.id,
                    user_id=grantee_user.id,
                    permission="project:update",
                    effect="grant",
                    project_id=UUID(invitation_app._test_project_id),
                    granted_by_user_id=UUID(invitation_app._test_admin_user_id),
                    granted_at=now,
                ),
            ]
        )

        project_row = db.session.get(ProjectModel, UUID(invitation_app._test_project_id))
        project_row.company_id = company.id

        from sqlalchemy import text

        # admin_user (project owner) needs an explicit assignment row: on SQLite,
        # ProjectMembershipReaderPort.is_member()'s raw-SQL owner-check UNION branch
        # compares `:uid`/`:pid` string literals against columns without the
        # dialect-normalization SqlAlchemyAuthzReader applies elsewhere, so it never
        # matches (see test_project_analyses_endpoints.py's analyses_app fixture,
        # same workaround) — the analyses CREATE route requires is_member() to pass.
        for uid in (UUID(invitation_app._test_admin_user_id), manager_user.id, member_user.id, grantee_user.id):
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, assigned_at) "
                    "VALUES (:uid, :pid, :at) "
                    "ON CONFLICT (user_id, project_id) DO NOTHING"
                ),
                {
                    "uid": str(uid),
                    "pid": invitation_app._test_project_id,
                    "at": now,
                },
            )
        db.session.commit()

        invitation_app._wg_manager_email = "wg_manager@invite-test.com"
        invitation_app._wg_member_email = "wg_member@invite-test.com"
        invitation_app._wg_grantee_email = "wg_grantee@invite-test.com"
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
def wg_grantee_token(inv_client, wg_app):
    """A company member holding a D8 grant of `project:update` on the project."""
    return _login(inv_client, wg_app._wg_grantee_email)


def _deny_project_update(app, monkeypatch) -> None:
    """Make authz_reader.grants_for answer with a D8 deny row for project:update.

    As if a company admin had denied that permission on this project (H3: deny
    must win over the matrix grant). Applied inside the test rather than in a
    fixture because it denies the permission to *every* caller, including the
    admin whose upload sets the scenario up (documents are `project:update`).
    """
    from wiring import get_container

    with app.app_context():
        reader = get_container().authz_reader

        def _fake_grants_for(user_id, company_id, project_id):
            return [("project:update", "deny")]

        monkeypatch.setattr(reader, "grants_for", _fake_grants_for)


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
        self, inv_client, admin_token, wg_manager_token, wg_app, monkeypatch
    ):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        _deny_project_update(wg_app, monkeypatch)
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


# ---------------------------------------------------------------------------
# D9 — documents are off-limits to a member, on every route
# ---------------------------------------------------------------------------


def _document_requests(pid: str, doc_id: str) -> list:
    """Every project_documents route, as `(label, method, url, kwargs)`.

    One row per route registered in `app/api/v1/project_documents/routes.py`:
    D9 gates the whole module on `project:update`, reads included, so the
    sweep below has to stay exhaustive.
    """
    base = f"/api/v1/projects/{pid}/documents"
    return [
        ("list", "get", base, {}),
        ("list_tags", "get", f"{base}/tags", {}),
        (
            "upload",
            "post",
            base,
            {
                "data": {"file": (io.BytesIO(b"member upload"), "member.pdf", "application/pdf")},
                "content_type": "multipart/form-data",
            },
        ),
        (
            "presign",
            "post",
            f"{base}/presign",
            {"json": {"filename": "m.pdf", "content_type": "application/pdf", "size_bytes": 10}},
        ),
        (
            "confirm",
            "post",
            f"{base}/confirm",
            {
                "json": {
                    "doc_id": str(uuid4()),
                    "storage_key": f"project-documents/{pid}/m.pdf",
                    "filename": "m.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 10,
                }
            },
        ),
        ("preview_url", "get", f"{base}/{doc_id}/preview-url", {}),
        ("download", "get", f"{base}/{doc_id}/download", {}),
        ("rename", "patch", f"{base}/{doc_id}/rename", {"json": {"filename": "renamed.pdf"}}),
        ("set_tags", "put", f"{base}/{doc_id}/tags", {"json": {"tags": ["plan"]}}),
        ("delete", "delete", f"{base}/{doc_id}", {}),
    ]


class TestDocumentsAreClosedToMembers:
    """D9: no list, no read, no download, no write — `project:update` or 403."""

    def test_member_is_refused_on_every_documents_route(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        refused = {}
        for label, method, url, kwargs in _document_requests(pid, doc_id):
            resp = getattr(inv_client, method)(url, headers=_auth(wg_member_token), **kwargs)
            refused[label] = resp.status_code
        assert refused == {label: 403 for label, *_ in _document_requests(pid, doc_id)}, refused

    def test_manager_reads_the_documents_a_member_cannot(self, inv_client, admin_token, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        assert inv_client.get(f"/api/v1/projects/{pid}/documents", headers=_auth(wg_manager_token)).status_code == 200
        resp = inv_client.get(f"/api/v1/projects/{pid}/documents/{doc_id}/download", headers=_auth(wg_manager_token))
        assert resp.status_code == 200

    def test_company_admin_reads_and_uploads(self, inv_client, admin_token, wg_app):
        pid = wg_app._test_project_id
        _upload_document(inv_client, admin_token, pid)  # 201 asserted in the helper
        assert inv_client.get(f"/api/v1/projects/{pid}/documents", headers=_auth(admin_token)).status_code == 200

    def test_d8_grant_of_project_update_opens_documents_to_a_member(
        self, inv_client, admin_token, wg_grantee_token, wg_app
    ):
        """No documents-specific permission: the D8 `project:update` grant is the key."""
        pid = wg_app._test_project_id
        doc_id = _upload_document(inv_client, admin_token, pid)
        assert inv_client.get(f"/api/v1/projects/{pid}/documents", headers=_auth(wg_grantee_token)).status_code == 200
        assert (
            inv_client.get(
                f"/api/v1/projects/{pid}/documents/{doc_id}/download", headers=_auth(wg_grantee_token)
            ).status_code
            == 200
        )
        resp = inv_client.patch(
            f"/api/v1/projects/{pid}/documents/{doc_id}/rename",
            json={"filename": "grantee.pdf"},
            headers=_auth(wg_grantee_token),
        )
        assert resp.status_code == 200, resp.get_json()

    def test_uploading_no_longer_buys_write_rights(self, inv_client, admin_token, wg_grantee_token, wg_app):
        """The grantee uploads, the grant is what lets them delete it — not authorship.

        A member without `project:update` cannot upload at all (swept above), so
        the use-case's uploader extra can only ever be additive now.
        """
        pid = wg_app._test_project_id
        resp = inv_client.post(
            f"/api/v1/projects/{pid}/documents",
            data={"file": (io.BytesIO(b"grantee upload"), "grantee-own.pdf", "application/pdf")},
            content_type="multipart/form-data",
            headers=_auth(wg_grantee_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        doc_id = resp.get_json()["id"]
        assert (
            inv_client.delete(f"/api/v1/projects/{pid}/documents/{doc_id}", headers=_auth(wg_grantee_token)).status_code
            == 204
        )


# ---------------------------------------------------------------------------
# D9 — notes / analyses / photos: a member reads the site journal, never writes
# ---------------------------------------------------------------------------


def _create_note(inv_client, token: str, project_id: str) -> str:
    resp = inv_client.post(
        f"/api/v1/projects/{project_id}/notes",
        json={"title": "Site journal entry"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


class TestSiteJournalReadsStayOpenToMembers:
    """Notes, analyses and photos keep answering `project:read` for a member."""

    def test_member_reads_notes(self, inv_client, wg_manager_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        _create_note(inv_client, wg_manager_token, pid)
        resp = inv_client.get(f"/api/v1/projects/{pid}/notes", headers=_auth(wg_member_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["count"] >= 1

    def test_member_reads_analyses(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        analysis_id = _upload_analysis(inv_client, admin_token, pid)
        headers = _auth(wg_member_token)
        assert inv_client.get(f"/api/v1/projects/{pid}/analyses", headers=headers).status_code == 200
        assert inv_client.get(f"/api/v1/projects/{pid}/analyses/tags", headers=headers).status_code == 200
        assert inv_client.get(f"/api/v1/projects/{pid}/analyses/{analysis_id}", headers=headers).status_code == 200
        assert (
            inv_client.get(f"/api/v1/projects/{pid}/analyses/{analysis_id}/content", headers=headers).status_code == 200
        )

    def test_member_reads_photos(self, inv_client, admin_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        photo_id = _upload_photo(inv_client, admin_token, pid)
        headers = _auth(wg_member_token)
        assert inv_client.get(f"/api/v1/projects/{pid}/photos", headers=headers).status_code == 200
        assert inv_client.get(f"/api/v1/projects/{pid}/photos/{photo_id}/thumbnail", headers=headers).status_code == 200
        assert inv_client.get(f"/api/v1/projects/{pid}/photos/{photo_id}/original", headers=headers).status_code == 200


class TestSiteJournalWritesNeedProjectUpdate:
    """Every create/update/delete on notes, analyses and photos is manager+."""

    def test_member_cannot_create_update_or_delete_a_note(self, inv_client, wg_manager_token, wg_member_token, wg_app):
        pid = wg_app._test_project_id
        note_id = _create_note(inv_client, wg_manager_token, pid)
        headers = _auth(wg_member_token)
        assert (
            inv_client.post(f"/api/v1/projects/{pid}/notes", json={"title": "Nope"}, headers=headers).status_code == 403
        )
        assert (
            inv_client.patch(
                f"/api/v1/projects/{pid}/notes/{note_id}", json={"title": "Nope"}, headers=headers
            ).status_code
            == 403
        )
        assert inv_client.delete(f"/api/v1/projects/{pid}/notes/{note_id}", headers=headers).status_code == 403

    def test_member_cannot_upload_an_analysis(self, inv_client, wg_member_token, wg_app):
        data = MultiDict()
        data.add("file", (io.BytesIO(b"<html><body>Nope</body></html>"), "nope.html", "text/html"))
        data.add("title", "Member Analysis")
        resp = inv_client.post(
            f"/api/v1/projects/{wg_app._test_project_id}/analyses",
            data=data,
            content_type="multipart/form-data",
            headers=_auth(wg_member_token),
        )
        assert resp.status_code == 403

    def test_member_cannot_upload_a_photo(self, inv_client, wg_member_token, wg_app):
        resp = inv_client.post(
            f"/api/v1/projects/{wg_app._test_project_id}/photos",
            data={"file": (io.BytesIO(_make_jpeg_bytes()), "member.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=_auth(wg_member_token),
        )
        assert resp.status_code == 403

    def test_manager_writes_the_whole_journal(self, inv_client, wg_manager_token, wg_app):
        pid = wg_app._test_project_id
        note_id = _create_note(inv_client, wg_manager_token, pid)
        assert (
            inv_client.patch(
                f"/api/v1/projects/{pid}/notes/{note_id}",
                json={"title": "Manager edit"},
                headers=_auth(wg_manager_token),
            ).status_code
            == 200
        )
        assert (
            inv_client.delete(f"/api/v1/projects/{pid}/notes/{note_id}", headers=_auth(wg_manager_token)).status_code
            == 204
        )
        _upload_analysis(inv_client, wg_manager_token, pid)
        _upload_photo(inv_client, wg_manager_token, pid)

    def test_company_admin_writes_the_whole_journal(self, inv_client, admin_token, wg_app):
        pid = wg_app._test_project_id
        note_id = _create_note(inv_client, admin_token, pid)
        assert (
            inv_client.delete(f"/api/v1/projects/{pid}/notes/{note_id}", headers=_auth(admin_token)).status_code == 204
        )
        _upload_analysis(inv_client, admin_token, pid)
        _upload_photo(inv_client, admin_token, pid)
