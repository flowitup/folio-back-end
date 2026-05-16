"""Integration tests for project document API endpoints.

Permission matrix and error-mapping coverage for:
  GET    /api/v1/projects/<pid>/documents
  POST   /api/v1/projects/<pid>/documents
  GET    /api/v1/projects/<pid>/documents/<did>/download
  DELETE /api/v1/projects/<pid>/documents/<did>

NOTE — SQLite UUID comparison caveat
-------------------------------------
SQLite stores UUIDs as hex strings without hyphens. The `ProjectModel.users`
relationship uses ORM-level comparison which does NOT work against raw-SQL
inserted rows on SQLite (PG works because the UUID type handles both forms).
Therefore, member membership in this test module is wired via ORM
`project_model.users.append(user)` (not raw SQL) so that `can_read_project`
correctly finds the member in `project.user_ids` under SQLite.

The admin user (project owner) always passes `can_read_project` via
`project.owner_id == user_id`, so admin tests are unaffected.
The superadmin user has `*:*` which bypasses `user_ids` check entirely.
"""

from __future__ import annotations

import io
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _docs_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/documents"


def _download_url(project_id: str, doc_id: str) -> str:
    return f"/api/v1/projects/{project_id}/documents/{doc_id}/download"


def _delete_url(project_id: str, doc_id: str) -> str:
    return f"/api/v1/projects/{project_id}/documents/{doc_id}"


def _make_upload_data(
    content: bytes = b"hello world",
    filename: str = "test.pdf",
    content_type: str = "application/pdf",
) -> dict:
    return {
        "file": (io.BytesIO(content), filename, content_type),
    }


def _upload(client, project_id: str, token: str, **kwargs) -> object:
    data = _make_upload_data(**kwargs)
    return client.post(
        _docs_url(project_id),
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token),
    )


def _upload_doc(client, project_id: str, token: str) -> str:
    resp = _upload(client, project_id, token)
    assert resp.status_code == 201, f"Setup upload failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["id"]


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


# ---------------------------------------------------------------------------
# Module-level app fixture with ORM-based membership
#
# We create our own app fixture here (rather than using invitation_app from
# conftest) so we can control membership wiring. invitation_app inserts
# memberships via raw SQL, which fails on SQLite UUID comparison in
# require_project_access. Here we use ORM relationship append instead.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def doc_app():
    """Flask app with in-memory DB wired with project_document use-cases.

    Users:
      - owner_user: project owner (can do everything by owner right)
      - member_user: ORM-wired project member (non-owner; uploader of member docs)
      - outsider_user: not a member of the project (gets 403 on all routes)
      - superadmin_user: has *:* permission
    """
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_invitation import SqlAlchemyInvitationRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.models import UserModel, RoleModel, PermissionModel, ProjectModel
    from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
    from app.infrastructure.database.repositories.sqlalchemy_project_document_repository import (
        SqlAlchemyProjectDocumentRepository,
    )
    from app.application.project_documents import (
        UploadProjectDocumentUseCase,
        ListProjectDocumentsUseCase,
        GetProjectDocumentUseCase,
        DeleteProjectDocumentUseCase,
    )
    from config import TestingConfig
    from wiring import configure_container, get_container
    import wiring as _wiring
    from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter

    class DocTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(DocTestConfig)

    with test_app.app_context():
        db.create_all()

        if _wiring._inmemory_email_adapter is None:
            _wiring._inmemory_email_adapter = InMemoryEmailAdapter()

        hasher = Argon2PasswordHasher()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        inv_repo = SqlAlchemyInvitationRepository(db.session)
        membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
        role_repo = SqlAlchemyRoleRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invitation_repo=inv_repo,
            project_membership_repo=membership_repo,
            role_repo=role_repo,
        )

        # Seed permissions + roles
        # NOTE: permission *names* must match what @require_permission checks exactly.
        # "project:read" is what the document routes declare; "*:*" grants wildcard.
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        owner_role = RoleModel(name="doc_owner", description="Owner")
        owner_role.permissions.append(read_perm)

        member_role = RoleModel(name="doc_member", description="Member")
        member_role.permissions.append(read_perm)

        superadmin_role = RoleModel(name="doc_superadmin", description="Superadmin")
        superadmin_role.permissions.append(star_perm)
        superadmin_role.permissions.append(read_perm)

        db.session.add_all([read_perm, star_perm, owner_role, member_role, superadmin_role])
        db.session.flush()

        # Seed users
        owner_user = UserModel(
            email="doc_owner@test.com",
            password_hash=hasher.hash("Owner1234!"),
            is_active=True,
        )
        owner_user.roles.append(owner_role)

        member_user = UserModel(
            email="doc_member@test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        member_user.roles.append(member_role)

        outsider_user = UserModel(
            email="doc_outsider@test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )

        superadmin_user = UserModel(
            email="doc_superadmin@test.com",
            password_hash=hasher.hash("Superadmin1234!"),
            is_active=True,
        )
        superadmin_user.roles.append(superadmin_role)

        db.session.add_all([owner_user, member_user, outsider_user, superadmin_user])
        db.session.flush()

        # Seed project
        project = ProjectModel(name="Doc Test Project", owner_id=owner_user.id)
        db.session.add(project)
        db.session.flush()

        # Add member via ORM relationship — avoids SQLite UUID comparison issue.
        # Raw SQL inserts store UUIDs as hex strings; ORM comparisons expect UUID
        # objects. Using ORM append keeps types consistent through the identity map.
        project.users.append(member_user)
        db.session.commit()

        # Wire project_document use-cases
        _c = get_container()
        _doc_repo = SqlAlchemyProjectDocumentRepository(db.session)
        _doc_storage = InMemoryDocumentStorage()
        _c.project_document_repository = _doc_repo
        _c.document_storage = _doc_storage
        _c.upload_project_document_usecase = UploadProjectDocumentUseCase(
            repo=_doc_repo, storage=_doc_storage, db_session=db.session
        )
        _c.list_project_documents_usecase = ListProjectDocumentsUseCase(repo=_doc_repo)
        _c.get_project_document_usecase = GetProjectDocumentUseCase(repo=_doc_repo, storage=_doc_storage)
        _c.delete_project_document_usecase = DeleteProjectDocumentUseCase(repo=_doc_repo, db_session=db.session)

        # Store test data on app for fixture access
        test_app._doc_owner_email = "doc_owner@test.com"
        test_app._doc_owner_password = "Owner1234!"
        test_app._doc_member_email = "doc_member@test.com"
        test_app._doc_member_password = "Member1234!"
        test_app._doc_outsider_email = "doc_outsider@test.com"
        test_app._doc_outsider_password = "Outsider1234!"
        test_app._doc_superadmin_email = "doc_superadmin@test.com"
        test_app._doc_superadmin_password = "Superadmin1234!"
        test_app._doc_project_id = str(project.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def doc_client(doc_app):
    return doc_app.test_client()


@pytest.fixture(scope="module")
def owner_token(doc_client, doc_app):
    return _login(doc_client, doc_app._doc_owner_email, doc_app._doc_owner_password)


@pytest.fixture(scope="module")
def member_token(doc_client, doc_app):
    return _login(doc_client, doc_app._doc_member_email, doc_app._doc_member_password)


@pytest.fixture(scope="module")
def outsider_token(doc_client, doc_app):
    return _login(doc_client, doc_app._doc_outsider_email, doc_app._doc_outsider_password)


@pytest.fixture(scope="module")
def superadmin_token(doc_client, doc_app):
    return _login(doc_client, doc_app._doc_superadmin_email, doc_app._doc_superadmin_password)


# ===========================================================================
# LIST  GET /api/v1/projects/<pid>/documents
# ===========================================================================


class TestListDocumentsPermissions:
    def test_200_owner_can_list(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data

    def test_200_member_can_list(self, doc_client, member_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200

    def test_403_non_member_cannot_list(self, doc_client, outsider_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, doc_app):
        # Use a fresh client with no session cookies to avoid cookie-based auth
        with doc_app.test_client() as fresh:
            resp = fresh.get(_docs_url(doc_app._doc_project_id))
        assert resp.status_code == 401

    def test_200_superadmin_can_list(self, doc_client, superadmin_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id),
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200


class TestListDocumentsValidation:
    def test_422_invalid_sort_param(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id) + "?sort=invalid_sort",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 422

    def test_422_per_page_exceeds_max(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id) + "?per_page=200",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 422

    def test_200_valid_sort_name(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id) + "?sort=name",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200

    def test_200_valid_sort_size(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id) + "?sort=size&order=asc",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200

    def test_200_multi_type_filter_returns_matching_kinds(self, doc_client, owner_token, doc_app):
        """?type=pdf&type=image should return 200 and filter by both kinds."""
        # Upload one pdf and one image
        _upload(doc_client, doc_app._doc_project_id, owner_token)  # pdf
        _upload(
            doc_client,
            doc_app._doc_project_id,
            owner_token,
            filename="photo.jpg",
            content_type="image/jpeg",
        )

        resp = doc_client.get(
            _docs_url(doc_app._doc_project_id) + "?type=pdf&type=image",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        for item in data["items"]:
            assert item["kind"] in ("pdf", "image")


# ===========================================================================
# UPLOAD  POST /api/v1/projects/<pid>/documents
# ===========================================================================


class TestUploadDocumentPermissions:
    def test_201_owner_can_upload(self, doc_client, owner_token, doc_app):
        resp = _upload(doc_client, doc_app._doc_project_id, owner_token)
        assert resp.status_code == 201

    def test_201_member_can_upload(self, doc_client, member_token, doc_app):
        resp = _upload(doc_client, doc_app._doc_project_id, member_token)
        assert resp.status_code == 201

    def test_403_non_member_cannot_upload(self, doc_client, outsider_token, doc_app):
        resp = _upload(doc_client, doc_app._doc_project_id, outsider_token)
        assert resp.status_code == 403

    def test_401_unauthenticated(self, doc_client, doc_app):
        resp = doc_client.post(
            _docs_url(doc_app._doc_project_id),
            data=_make_upload_data(),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 401

    def test_201_superadmin_can_upload(self, doc_client, superadmin_token, doc_app):
        resp = _upload(doc_client, doc_app._doc_project_id, superadmin_token)
        assert resp.status_code == 201


class TestUploadDocumentErrorCases:
    def test_400_missing_file_field(self, doc_client, owner_token, doc_app):
        resp = doc_client.post(
            _docs_url(doc_app._doc_project_id),
            data={},
            content_type="multipart/form-data",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 400

    def test_201_15mb_file_within_flask_cap(self, doc_client, owner_token, doc_app):
        """15 MB file must reach the use-case and succeed.

        Regression for C1: previously Flask's 10 MiB MAX_CONTENT_LENGTH would
        return 413 before the route handler ran, breaking the advertised 25 MB cap.
        This test proves the new 26 MiB Flask cap allows a 15 MB file through.
        """
        content_15mb = b"x" * (15 * 1024 * 1024)
        resp = _upload(
            doc_client,
            doc_app._doc_project_id,
            owner_token,
            content=content_15mb,
        )
        assert resp.status_code == 201

    def test_413_oversize_file(self, doc_client, owner_token, doc_app):
        """File exceeding MAX_SIZE_BYTES should return 413."""
        from app.application.project_documents import MAX_SIZE_BYTES

        oversize_content = b"x" * (MAX_SIZE_BYTES + 1)
        resp = _upload(
            doc_client,
            doc_app._doc_project_id,
            owner_token,
            content=oversize_content,
        )
        assert resp.status_code == 413

    def test_415_unsupported_type(self, doc_client, owner_token, doc_app):
        """Executable file upload should return 415."""
        resp = _upload(
            doc_client,
            doc_app._doc_project_id,
            owner_token,
            filename="malware.exe",
            content_type="application/octet-stream",
        )
        assert resp.status_code == 415

    def test_201_response_shape(self, doc_client, owner_token, doc_app):
        resp = _upload(doc_client, doc_app._doc_project_id, owner_token)
        assert resp.status_code == 201
        data = resp.get_json()
        required_keys = {
            "id",
            "project_id",
            "filename",
            "content_type",
            "size_bytes",
            "kind",
            "uploaded_at",
            "uploader_id",
            "download_url",
        }
        assert required_keys.issubset(data.keys())

    def test_415_empty_filename_after_sanitation(self, doc_client, owner_token, doc_app):
        """A file with no valid characters after sanitation should return 415."""
        resp = doc_client.post(
            _docs_url(doc_app._doc_project_id),
            data={"file": (io.BytesIO(b"data"), "../../", "application/pdf")},
            content_type="multipart/form-data",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 415


class TestUploadRateLimit:
    def test_rate_limit_429_after_30_requests(self):
        """31st upload in <60s should return 429. Uses a dedicated rate-limit-enabled app."""
        pytest.skip(
            "Rate-limit test requires RATELIMIT_ENABLED=True. "
            "The global test fixture uses RATELIMIT_ENABLED=False to prevent "
            "cross-test interference. Run manually with a dedicated fixture if needed."
            # NOTE: A dedicated rate-limit fixture IS implemented in
            # tests/test_auth_endpoints.py::TestRateLimiting as a reference pattern.
        )


# ===========================================================================
# DOWNLOAD  GET /api/v1/projects/<pid>/documents/<did>/download
# ===========================================================================


class TestDownloadDocumentPermissions:
    def test_200_owner_can_download(self, doc_client, owner_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 200

    def test_200_member_can_download(self, doc_client, owner_token, member_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, doc_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200

    def test_403_non_member_cannot_download(self, doc_client, owner_token, outsider_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, doc_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, doc_client, owner_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        with doc_app.test_client() as fresh:
            resp = fresh.get(_download_url(doc_app._doc_project_id, doc_id))
        assert resp.status_code == 401

    def test_200_superadmin_can_download(self, doc_client, owner_token, superadmin_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, doc_id),
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200


class TestDownloadDocumentErrors:
    def test_404_nonexistent_doc(self, doc_client, owner_token, doc_app):
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, str(uuid4())),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 404

    def test_404_cross_project_id_mismatch(self, doc_client, owner_token, doc_app):
        """Doc from this project should return 404 when accessed via a different project URL."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)

        fake_project_id = str(uuid4())
        resp = doc_client.get(
            _download_url(fake_project_id, doc_id),
            headers=_auth(owner_token),
        )
        # The route may return 404 (project not found) or 404 (cross-project guard)
        assert resp.status_code == 404

    def test_200_download_returns_file_content(self, doc_client, owner_token, doc_app):
        content = b"exact document content bytes"
        resp = _upload(
            doc_client,
            doc_app._doc_project_id,
            owner_token,
            content=content,
        )
        assert resp.status_code == 201
        doc_id = resp.get_json()["id"]

        dl_resp = doc_client.get(
            _download_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )
        assert dl_resp.status_code == 200
        assert dl_resp.data == content


# ===========================================================================
# DELETE  DELETE /api/v1/projects/<pid>/documents/<did>
# ===========================================================================


class TestDeleteDocumentPermissions:
    def test_204_uploader_can_delete_own_doc(self, doc_client, member_token, doc_app):
        """Member uploads their own doc and deletes it — 204."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, member_token)
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 204

    def test_403_member_cannot_delete_other_members_doc(self, doc_client, owner_token, member_token, doc_app):
        """Owner uploads doc; member (non-owner, non-uploader) tries to delete — 403."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_204_owner_can_delete_any_doc(self, doc_client, member_token, owner_token, doc_app):
        """Project owner can delete any doc — 204."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, member_token)
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 204

    def test_204_superadmin_can_delete_any_doc(self, doc_client, member_token, superadmin_token, doc_app):
        """Superadmin (*:*) can delete any doc — 204."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, member_token)
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 204

    def test_403_non_member_cannot_delete(self, doc_client, owner_token, outsider_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_401_unauthenticated(self, doc_client, owner_token, doc_app):
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)
        resp = doc_client.delete(_delete_url(doc_app._doc_project_id, doc_id))
        assert resp.status_code == 401


class TestDeleteDocumentErrors:
    def test_404_nonexistent_doc(self, doc_client, owner_token, doc_app):
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, str(uuid4())),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 404

    def test_404_already_soft_deleted(self, doc_client, owner_token, doc_app):
        """Second delete on same doc should return 404."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)

        # First delete — succeeds
        resp1 = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )
        assert resp1.status_code == 204

        # Second delete — doc is soft-deleted → 404
        resp2 = doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )
        assert resp2.status_code == 404

    def test_204_deletes_doc_from_list(self, doc_client, owner_token, doc_app):
        """After deletion, doc should not appear in list."""
        doc_id = _upload_doc(doc_client, doc_app._doc_project_id, owner_token)

        doc_client.delete(
            _delete_url(doc_app._doc_project_id, doc_id),
            headers=_auth(owner_token),
        )

        list_resp = doc_client.get(
            _docs_url(doc_app._doc_project_id),
            headers=_auth(owner_token),
        )
        assert list_resp.status_code == 200
        ids = [d["id"] for d in list_resp.get_json()["items"]]
        assert doc_id not in ids

    def test_400_invalid_uuid_for_delete(self, doc_client, owner_token, doc_app):
        """Non-UUID document_id in DELETE URL → 400."""
        resp = doc_client.delete(
            _delete_url(doc_app._doc_project_id, "not-a-uuid"),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 400

    def test_400_invalid_uuid_for_download(self, doc_client, owner_token, doc_app):
        """Non-UUID document_id in download URL → 400."""
        resp = doc_client.get(
            _download_url(doc_app._doc_project_id, "not-a-uuid"),
            headers=_auth(owner_token),
        )
        assert resp.status_code == 400

    def test_400_empty_filename_in_upload(self, doc_client, owner_token, doc_app):
        """Upload with a file that has an empty filename → 400."""
        resp = doc_client.post(
            _docs_url(doc_app._doc_project_id),
            data={"file": (io.BytesIO(b"data"), "", "application/pdf")},
            content_type="multipart/form-data",
            headers=_auth(owner_token),
        )
        assert resp.status_code == 400
