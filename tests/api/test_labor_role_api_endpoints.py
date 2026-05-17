"""API-level integration tests for labor role endpoints.

Covers:
- GET  /api/v1/labor/roles        → 200 with roles list + palette
- POST /api/v1/labor/roles        → 201 (valid), 409 (duplicate), 400/422 (invalid)
- PATCH /api/v1/labor/roles/<id>  → 200 (valid), 404 (not found), 409 (conflict)
- DELETE /api/v1/labor/roles/<id> → 204, 404
- Worker CREATE with role_id      → response includes role_name, role_color
- Worker UPDATE with role_id      → assign, reassign, clear
- Regression: PATCH worker with ONLY role_id → other fields not dropped
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    UserModel,
    ProjectModel,
    RoleModel,
    PermissionModel,
)
from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def role_app():
    """Flask app wired with in-memory DB + full labor + labor-role container."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from config import TestingConfig
    from wiring import configure_container

    class RoleTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(RoleTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        manage_labor_perm = PermissionModel(name="project:manage_labor", resource="project", action="manage_labor")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="lr_admin", description="LR Admin")
        admin_role.permissions.append(manage_labor_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(star_perm)

        db.session.add_all([manage_labor_perm, read_perm, star_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="lradmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        project = ProjectModel(
            name="Labor Role API Test Project",
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
            worker_repository=worker_repo,
            labor_entry_repository=entry_repo,
        )

        # Wire labor role use-cases post-configure_container
        from wiring import get_container as _get_container
        from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase as _CreateLRUC
        from app.application.labor.update_labor_role_usecase import UpdateLaborRoleUseCase as _UpdateLRUC
        from app.application.labor.delete_labor_role_usecase import DeleteLaborRoleUseCase as _DeleteLRUC
        from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase as _ListLRUC

        _c = _get_container()
        _labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
        _c.labor_role_repository = _labor_role_repo
        _c.create_labor_role_usecase = _CreateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.update_labor_role_usecase = _UpdateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.delete_labor_role_usecase = _DeleteLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.list_labor_roles_usecase = _ListLRUC(repo=_labor_role_repo)

        test_app._test_admin_email = "lradmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def role_client(role_app):
    return role_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(role_client, role_app):
    return _login(role_client, role_app._test_admin_email, role_app._test_admin_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_ROLES_URL = "/api/v1/labor/roles"


def _role_url(role_id: str) -> str:
    return f"/api/v1/labor/roles/{role_id}"


def _workers_url(pid: str) -> str:
    return f"/api/v1/projects/{pid}/workers"


def _worker_url(pid: str, wid: str) -> str:
    return f"/api/v1/projects/{pid}/workers/{wid}"


# ---------------------------------------------------------------------------
# GET /labor/roles
# ---------------------------------------------------------------------------


class TestListLaborRoles:
    def test_list_returns_200_with_empty_roles_and_palette(self, role_client, admin_token):
        resp = role_client.get(_ROLES_URL, headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "roles" in data
        assert "palette" in data
        assert isinstance(data["palette"], list)
        assert len(data["palette"]) > 0  # palette is never empty

    def test_list_returns_created_roles(self, role_client, admin_token):
        # Create two roles first
        role_client.post(_ROLES_URL, json={"name": "Listed A", "color": "#E11D48"}, headers=_auth(admin_token))
        role_client.post(_ROLES_URL, json={"name": "Listed B", "color": "#7C3AED"}, headers=_auth(admin_token))

        resp = role_client.get(_ROLES_URL, headers=_auth(admin_token))
        assert resp.status_code == 200
        names = [r["name"] for r in resp.get_json()["roles"]]
        assert "Listed A" in names
        assert "Listed B" in names

    def test_list_palette_contains_hex_colors(self, role_client, admin_token):
        import re

        resp = role_client.get(_ROLES_URL, headers=_auth(admin_token))
        palette = resp.get_json()["palette"]
        for color in palette:
            assert re.match(r"^#[0-9a-fA-F]{6}$", color), f"Invalid hex in palette: {color}"


# ---------------------------------------------------------------------------
# POST /labor/roles
# ---------------------------------------------------------------------------


class TestCreateLaborRole:
    def test_create_valid_role_returns_201(self, role_client, admin_token):
        resp = role_client.post(
            _ROLES_URL,
            json={"name": "Thợ chính", "color": "#E11D48"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Thợ chính"
        assert data["color"] == "#E11D48"
        assert "id" in data
        assert "created_at" in data

    def test_create_duplicate_name_returns_409(self, role_client, admin_token):
        role_client.post(_ROLES_URL, json={"name": "DupRole", "color": "#E11D48"}, headers=_auth(admin_token))
        resp = role_client.post(_ROLES_URL, json={"name": "DupRole", "color": "#0EA5E9"}, headers=_auth(admin_token))
        assert resp.status_code == 409

    def test_create_invalid_color_returns_422_or_400(self, role_client, admin_token):
        resp = role_client.post(
            _ROLES_URL,
            json={"name": "Bad Color Role", "color": "not-a-hex"},
            headers=_auth(admin_token),
        )
        assert resp.status_code in (400, 422)

    def test_create_empty_name_returns_400(self, role_client, admin_token):
        resp = role_client.post(
            _ROLES_URL,
            json={"name": "   ", "color": "#E11D48"},
            headers=_auth(admin_token),
        )
        assert resp.status_code in (400, 422)

    def test_create_missing_color_returns_422(self, role_client, admin_token):
        resp = role_client.post(
            _ROLES_URL,
            json={"name": "No Color Role"},
            headers=_auth(admin_token),
        )
        assert resp.status_code in (400, 422)

    def test_create_name_too_long_returns_400(self, role_client, admin_token):
        resp = role_client.post(
            _ROLES_URL,
            json={"name": "x" * 101, "color": "#E11D48"},
            headers=_auth(admin_token),
        )
        assert resp.status_code in (400, 422)

    def test_create_requires_auth(self, role_client):
        resp = role_client.post(_ROLES_URL, json={"name": "Unauthed", "color": "#E11D48"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /labor/roles/<id>
# ---------------------------------------------------------------------------


class TestUpdateLaborRole:
    def _create_role(self, role_client, admin_token, name: str, color: str = "#E11D48") -> str:
        resp = role_client.post(_ROLES_URL, json={"name": name, "color": color}, headers=_auth(admin_token))
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_update_name_returns_200(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "OriginalName")
        resp = role_client.patch(
            _role_url(role_id),
            json={"name": "UpdatedName"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "UpdatedName"

    def test_update_color_only(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "ColorOnlyRole", "#E11D48")
        resp = role_client.patch(
            _role_url(role_id),
            json={"color": "#10B981"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["color"] == "#10B981"
        assert data["name"] == "ColorOnlyRole"

    def test_update_not_found_returns_404(self, role_client, admin_token):
        resp = role_client.patch(
            _role_url(str(uuid4())),
            json={"name": "Ghost"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_update_rename_conflict_returns_409(self, role_client, admin_token):
        self._create_role(role_client, admin_token, "ConflictRoleA")
        role_b_id = self._create_role(role_client, admin_token, "ConflictRoleB")
        resp = role_client.patch(
            _role_url(role_b_id),
            json={"name": "ConflictRoleA"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409

    def test_update_invalid_color_returns_400(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "BadColorUpdate")
        resp = role_client.patch(
            _role_url(role_id),
            json={"color": "bad"},
            headers=_auth(admin_token),
        )
        assert resp.status_code in (400, 422)

    def test_update_requires_auth(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "UnauthPatch")
        resp = role_client.patch(_role_url(role_id), json={"name": "No Auth"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /labor/roles/<id>
# ---------------------------------------------------------------------------


class TestDeleteLaborRole:
    def _create_role(self, role_client, admin_token, name: str) -> str:
        resp = role_client.post(_ROLES_URL, json={"name": name, "color": "#E11D48"}, headers=_auth(admin_token))
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_delete_returns_204(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "ToDeleteRole")
        resp = role_client.delete(_role_url(role_id), headers=_auth(admin_token))
        assert resp.status_code == 204

    def test_delete_actually_removes_role(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "ActuallyDeleted")
        role_client.delete(_role_url(role_id), headers=_auth(admin_token))

        # Verify it no longer appears in the list
        list_resp = role_client.get(_ROLES_URL, headers=_auth(admin_token))
        ids = [r["id"] for r in list_resp.get_json()["roles"]]
        assert role_id not in ids

    def test_delete_not_found_returns_404(self, role_client, admin_token):
        resp = role_client.delete(_role_url(str(uuid4())), headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_delete_requires_auth(self, role_client, admin_token):
        role_id = self._create_role(role_client, admin_token, "UnauthDelete")
        resp = role_client.delete(_role_url(role_id))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Worker CREATE / UPDATE with role_id integration
# ---------------------------------------------------------------------------


class TestWorkerRoleIntegration:
    def _create_labor_role(self, role_client, admin_token, name: str) -> str:
        resp = role_client.post(_ROLES_URL, json={"name": name, "color": "#E11D48"}, headers=_auth(admin_token))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()["id"]

    def _create_worker(self, role_client, admin_token, role_app, name: str, role_id: str | None = None) -> dict:
        pid = role_app._test_project_id
        payload: dict = {"name": name, "daily_rate": 100.0}
        if role_id is not None:
            payload["role_id"] = role_id
        resp = role_client.post(_workers_url(pid), json=payload, headers=_auth(admin_token))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()

    def test_create_worker_with_role_id_returns_role_fields(self, role_client, admin_token, role_app):
        """POST worker with role_id → response includes role_name and role_color."""
        role_id = self._create_labor_role(role_client, admin_token, "Thợ chính Create")
        data = self._create_worker(role_client, admin_token, role_app, "Wk With Role", role_id)

        assert data["role_id"] == role_id
        assert data["role_name"] == "Thợ chính Create"
        assert data["role_color"] == "#E11D48"

    def test_create_worker_without_role_id_returns_null_role_fields(self, role_client, admin_token, role_app):
        """POST worker without role_id → role_* fields are null."""
        data = self._create_worker(role_client, admin_token, role_app, "Wk No Role")

        assert data["role_id"] is None
        assert data["role_name"] is None
        assert data["role_color"] is None

    def test_update_worker_assigns_role(self, role_client, admin_token, role_app):
        """PUT worker with role_id → role fields appear in response."""
        pid = role_app._test_project_id
        role_id = self._create_labor_role(role_client, admin_token, "Assign Role")
        worker = self._create_worker(role_client, admin_token, role_app, "Assign Worker")

        resp = role_client.put(
            _worker_url(pid, worker["id"]),
            json={"role_id": role_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role_id"] == role_id
        assert data["role_name"] == "Assign Role"

    def test_update_worker_reassigns_role(self, role_client, admin_token, role_app):
        """PUT worker changing role_id → new role fields in response."""
        pid = role_app._test_project_id
        role_a_id = self._create_labor_role(role_client, admin_token, "Role Alpha")
        role_b_id = self._create_labor_role(role_client, admin_token, "Role Beta")

        # Create worker with role A
        worker = self._create_worker(role_client, admin_token, role_app, "Reassign Worker", role_a_id)

        # Reassign to role B
        resp = role_client.put(
            _worker_url(pid, worker["id"]),
            json={"role_id": role_b_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["role_id"] == role_b_id
        assert resp.get_json()["role_name"] == "Role Beta"

    def test_update_worker_clears_role(self, role_client, admin_token, role_app):
        """PUT worker with role_id=null → role fields become null."""
        pid = role_app._test_project_id
        role_id = self._create_labor_role(role_client, admin_token, "Role To Clear")
        worker = self._create_worker(role_client, admin_token, role_app, "Clear Role Worker", role_id)

        # Clear the role by sending role_id=null
        resp = role_client.put(
            _worker_url(pid, worker["id"]),
            json={"role_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role_id"] is None
        assert data["role_name"] is None
        assert data["role_color"] is None

    def test_patch_worker_only_role_id_does_not_drop_other_fields(self, role_client, admin_token, role_app):
        """Regression: PATCH with ONLY role_id must not drop name/daily_rate/phone.

        Prior to the sentinel fix in UpdateWorkerUseCase, passing an omitted
        role_id would silently clear it. This test verifies the inverse: that
        sending ONLY role_id does not wipe name or daily_rate.
        """
        pid = role_app._test_project_id
        role_id = self._create_labor_role(role_client, admin_token, "Sentinel Role")

        # Create worker with known name and rate
        worker = self._create_worker(role_client, admin_token, role_app, "Sentinel Worker")
        # Confirm initial state (no role)
        assert worker["name"] == "Sentinel Worker"
        assert worker["daily_rate"] == 100.0
        assert worker["role_id"] is None

        # PATCH with only role_id
        resp = role_client.put(
            _worker_url(pid, worker["id"]),
            json={"role_id": role_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()

        # role got assigned
        assert data["role_id"] == role_id
        # name and daily_rate must be preserved
        assert data["name"] == "Sentinel Worker"
        assert data["daily_rate"] == 100.0

    def test_update_worker_role_id_omitted_preserves_existing_role(self, role_client, admin_token, role_app):
        """PUT without role_id key in body → existing role must be preserved (sentinel behavior)."""
        pid = role_app._test_project_id
        role_id = self._create_labor_role(role_client, admin_token, "Preserve Role")
        worker = self._create_worker(role_client, admin_token, role_app, "Preserve Worker", role_id)
        assert worker["role_id"] == role_id

        # PUT body does NOT include role_id key at all → sentinel path: leave unchanged
        resp = role_client.put(
            _worker_url(pid, worker["id"]),
            json={"name": "Preserve Worker Updated"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Preserve Worker Updated"
        # role must still be set (not cleared by omission)
        assert data["role_id"] == role_id
