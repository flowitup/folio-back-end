"""Integration tests for project-scoped tags endpoints (5 routes).

Covers:
- POST /api/v1/projects/<pid>/tags — create tag
- GET /api/v1/projects/<pid>/tags — list tags
- PUT /api/v1/projects/<pid>/tags/<tag_id> — update tag
- DELETE /api/v1/projects/<pid>/tags/<tag_id> — delete tag
- GET /api/v1/projects/<pid>/tag-summary — cost rollup by tag
"""

from __future__ import annotations

from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _tags_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/tags"


def _tag_url(project_id: str, tag_id: str) -> str:
    return f"/api/v1/projects/{project_id}/tags/{tag_id}"


def _summary_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/tag-summary"


def _valid_create_body(**overrides) -> dict:
    base = {
        "name": "Test Tag",
        "color": "#FF5733",
    }
    base.update(overrides)
    return base


def _valid_update_body(**overrides) -> dict:
    base = {
        "name": None,
        "color": None,
    }
    base.update(overrides)
    return base


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


# ---------------------------------------------------------------------------
# Flask app + fixtures for tags endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def tags_app():
    """Flask app with in-memory DB + full tags container for route tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
        SqlAlchemyProjectTagRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership_reader import (
        SqlAlchemyProjectMembershipReader,
    )
    from app.application.tags.create_project_tag_usecase import CreateProjectTagUseCase
    from app.application.tags.list_project_tags_usecase import ListProjectTagsUseCase
    from app.application.tags.update_project_tag_usecase import UpdateProjectTagUseCase
    from app.application.tags.delete_project_tag_usecase import DeleteProjectTagUseCase
    from app.application.tags.tag_summary_usecase import TagSummaryUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class TagsTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(TagsTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)

        # Seed permissions and roles
        from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel, ProjectModel

        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="tags_admin", description="Tags Admin")
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(star_perm)

        db.session.add_all([read_perm, star_perm, admin_role])
        db.session.flush()

        # Seed users
        admin_user = UserModel(
            email="tagsadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        outsider_user = UserModel(
            email="outsider@test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )

        db.session.add_all([admin_user, outsider_user])
        db.session.flush()

        # Seed projects
        project1 = ProjectModel(
            name="Tags Test Project 1",
            owner_id=admin_user.id,
        )
        project2 = ProjectModel(
            name="Tags Test Project 2",
            owner_id=admin_user.id,
        )
        db.session.add_all([project1, project2])
        db.session.commit()

        # Add admin_user as member of both projects
        from sqlalchemy import text
        from datetime import datetime, timezone

        for project in [project1, project2]:
            db.session.execute(
                text(
                    "INSERT INTO user_projects "
                    "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                    "VALUES (:uid, :pid, :rid, NULL, :at) "
                    "ON CONFLICT (user_id, project_id) DO NOTHING"
                ),
                {
                    "uid": str(admin_user.id),
                    "pid": str(project.id),
                    "rid": str(admin_role.id),
                    "at": datetime.now(timezone.utc),
                },
            )
        db.session.commit()

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=token_issuer,
            session_manager=FlaskSessionManager(),
        )

        # Wire tags use-cases
        _c = get_container()
        _tag_repo = SqlAlchemyProjectTagRepository(db.session)
        _membership_reader = SqlAlchemyProjectMembershipReader(db.session)

        _c.create_project_tag_usecase = CreateProjectTagUseCase(
            tag_repo=_tag_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.list_project_tags_usecase = ListProjectTagsUseCase(
            tag_repo=_tag_repo,
            membership_reader=_membership_reader,
        )
        _c.update_project_tag_usecase = UpdateProjectTagUseCase(
            tag_repo=_tag_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.delete_project_tag_usecase = DeleteProjectTagUseCase(
            tag_repo=_tag_repo,
            membership_reader=_membership_reader,
            db_session=db.session,
        )
        _c.tag_summary_usecase = TagSummaryUseCase(
            tag_repo=_tag_repo,
            labor_reader=_tag_repo,
            expense_reader=_tag_repo,
            membership_reader=_membership_reader,
        )

        test_app._test_admin_email = "tagsadmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_outsider_email = "outsider@test.com"
        test_app._test_outsider_password = "Outsider1234!"
        test_app._test_project_1_id = str(project1.id)
        test_app._test_project_2_id = str(project2.id)
        test_app._test_admin_user_id = str(admin_user.id)
        test_app._test_outsider_user_id = str(outsider_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def tags_client(tags_app):
    return tags_app.test_client()


@pytest.fixture
def admin_token(tags_client, tags_app):
    return _login(tags_client, tags_app._test_admin_email, tags_app._test_admin_password)


@pytest.fixture
def outsider_token(tags_client, tags_app):
    return _login(tags_client, tags_app._test_outsider_email, tags_app._test_outsider_password)


# ===========================================================================
# POST /api/v1/projects/<project_id>/tags — create tag
# ===========================================================================


class TestCreateTagEndpoint:
    def test_201_admin_creates_tag(self, tags_client, admin_token, tags_app):
        """Admin member creates a tag successfully."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="Fondations", color="#FF0000"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Fondations"
        assert data["color"] == "#FF0000"
        assert "id" in data
        assert "project_id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_201_response_shape_complete(self, tags_client, admin_token, tags_app):
        """Response includes all required fields."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        required_keys = {
            "id",
            "project_id",
            "name",
            "color",
            "created_at",
            "updated_at",
        }
        assert required_keys.issubset(data.keys())

    def test_201_project_id_matches_url(self, tags_client, admin_token, tags_app):
        """Returned project_id matches the URL parameter."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["project_id"] == tags_app._test_project_1_id

    def test_401_unauthenticated(self, tags_client, tags_app):
        """Unauthenticated request returns 401."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
        )
        assert resp.status_code == 401

    def test_403_non_member(self, tags_client, outsider_token, tags_app):
        """Non-member user gets 403 Forbidden."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert "Forbidden" in data["error"]

    def test_409_duplicate_name_same_project(self, tags_client, admin_token, tags_app):
        """Creating a tag with duplicate name in same project returns 409."""
        # Create first tag
        resp1 = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="DuplicateName"),
            headers=_auth(admin_token),
        )
        assert resp1.status_code == 201

        # Try to create second tag with same name
        resp2 = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="DuplicateName", color="#00FF00"),
            headers=_auth(admin_token),
        )
        assert resp2.status_code == 409
        data = resp2.get_json()
        assert "Conflict" in data["error"]
        assert "already exists" in data["message"]

    def test_201_same_name_different_project_allowed(self, tags_client, admin_token, tags_app):
        """Same tag name is allowed in different projects."""
        # Create tag in project 1
        resp1 = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="SameName"),
            headers=_auth(admin_token),
        )
        assert resp1.status_code == 201

        # Create tag with same name in project 2 — should succeed
        resp2 = tags_client.post(
            _tags_url(tags_app._test_project_2_id),
            json=_valid_create_body(name="SameName", color="#00FF00"),
            headers=_auth(admin_token),
        )
        assert resp2.status_code == 201
        assert resp2.get_json()["name"] == "SameName"

    def test_422_missing_name(self, tags_client, admin_token, tags_app):
        """Missing name field returns 422."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json={"color": "#FF0000"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_422_missing_color(self, tags_client, admin_token, tags_app):
        """Missing color field returns 422."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json={"name": "Tag"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_422_invalid_color_format(self, tags_client, admin_token, tags_app):
        """Invalid color format returns 422."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(color="not_hex"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_422_name_empty_string(self, tags_client, admin_token, tags_app):
        """Empty string for name returns 422."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name=""),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_422_name_exceeds_max_length(self, tags_client, admin_token, tags_app):
        """Name exceeding 100 chars returns 422."""
        resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="a" * 101),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422


# ===========================================================================
# GET /api/v1/projects/<project_id>/tags — list tags
# ===========================================================================


class TestListTagsEndpoint:
    def test_200_empty_list(self, tags_client, admin_token, tags_app):
        """Empty project returns empty list."""
        resp = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["items"] == []

    def test_200_list_single_tag(self, tags_client, admin_token, tags_app):
        """List returns single tag."""
        # Create a tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="TestTag"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # List tags
        resp = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == tag_id
        assert data["items"][0]["name"] == "TestTag"

    def test_200_list_multiple_tags_sorted_by_name(self, tags_client, admin_token, tags_app):
        """List returns multiple tags sorted by name."""
        names = ["Charpente", "Fondations", "Couverture"]
        for name in names:
            tags_client.post(
                _tags_url(tags_app._test_project_1_id),
                json=_valid_create_body(name=name),
                headers=_auth(admin_token),
            )

        resp = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 3
        returned_names = [item["name"] for item in data["items"]]
        # Should be sorted alphabetically
        assert returned_names == sorted(names)

    def test_200_project_isolation(self, tags_client, admin_token, tags_app):
        """Tags in project 1 don't appear in project 2 list."""
        # Create tag in project 1
        tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="Project1Tag"),
            headers=_auth(admin_token),
        )

        # Create tag in project 2
        tags_client.post(
            _tags_url(tags_app._test_project_2_id),
            json=_valid_create_body(name="Project2Tag"),
            headers=_auth(admin_token),
        )

        # List project 1 tags
        resp1 = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        names1 = [item["name"] for item in resp1.get_json()["items"]]
        assert "Project1Tag" in names1
        assert "Project2Tag" not in names1

        # List project 2 tags
        resp2 = tags_client.get(
            _tags_url(tags_app._test_project_2_id),
            headers=_auth(admin_token),
        )
        names2 = [item["name"] for item in resp2.get_json()["items"]]
        assert "Project2Tag" in names2
        assert "Project1Tag" not in names2

    def test_401_unauthenticated(self, tags_client, tags_app):
        """Unauthenticated request returns 401."""
        resp = tags_client.get(_tags_url(tags_app._test_project_1_id))
        assert resp.status_code == 401

    def test_403_non_member(self, tags_client, outsider_token, tags_app):
        """Non-member user gets 403 Forbidden."""
        resp = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403


# ===========================================================================
# PUT /api/v1/projects/<project_id>/tags/<tag_id> — update tag
# ===========================================================================


class TestUpdateTagEndpoint:
    def test_200_update_name_only(self, tags_client, admin_token, tags_app):
        """Update tag name successfully."""
        # Create tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="OldName"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Update name
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(name="NewName"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "NewName"
        assert data["id"] == tag_id

    def test_200_update_color_only(self, tags_client, admin_token, tags_app):
        """Update tag color successfully."""
        # Create tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(color="#FF0000"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Update color
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(color="#00FF00"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["color"] == "#00FF00"
        assert data["id"] == tag_id

    def test_200_update_both_fields(self, tags_client, admin_token, tags_app):
        """Update both name and color."""
        # Create tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="Original", color="#FF0000"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Update both
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(name="Updated", color="#0000FF"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Updated"
        assert data["color"] == "#0000FF"

    def test_404_non_existent_tag(self, tags_client, admin_token, tags_app):
        """Update non-existent tag returns 404."""
        fake_tag_id = str(uuid4())
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, fake_tag_id),
            json=_valid_update_body(name="NewName"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_409_rename_to_duplicate_name(self, tags_client, admin_token, tags_app):
        """Renaming tag to existing name in same project returns 409."""
        # Create two tags
        resp1 = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="Tag1"),
            headers=_auth(admin_token),
        )
        tag_id_1 = resp1.get_json()["id"]

        tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="Tag2"),
            headers=_auth(admin_token),
        )

        # Try to rename Tag1 to Tag2 — should fail
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id_1),
            json=_valid_update_body(name="Tag2"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409
        assert "Conflict" in resp.get_json()["error"]

    def test_200_rename_to_same_name_allowed(self, tags_client, admin_token, tags_app):
        """Updating tag to its own name is allowed (no-op allowed)."""
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="TagName"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Update to the same name
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(name="TagName"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "TagName"

    def test_403_non_member(self, tags_client, admin_token, outsider_token, tags_app):
        """Non-member cannot update tags."""
        # Create tag as admin
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Try to update as outsider
        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(name="NewName"),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_422_invalid_color_format_on_update(self, tags_client, admin_token, tags_app):
        """Invalid color format on update returns 422."""
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        resp = tags_client.put(
            _tag_url(tags_app._test_project_1_id, tag_id),
            json=_valid_update_body(color="badcolor"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422


# ===========================================================================
# DELETE /api/v1/projects/<project_id>/tags/<tag_id> — delete tag
# ===========================================================================


class TestDeleteTagEndpoint:
    def test_204_delete_tag(self, tags_client, admin_token, tags_app):
        """Delete tag returns 204."""
        # Create tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Delete tag
        resp = tags_client.delete(
            _tag_url(tags_app._test_project_1_id, tag_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204

    def test_tag_removed_from_list_after_delete(self, tags_client, admin_token, tags_app):
        """Tag is no longer returned by list endpoint after deletion."""
        # Create tag
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="ToDelete"),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Delete tag
        tags_client.delete(
            _tag_url(tags_app._test_project_1_id, tag_id),
            headers=_auth(admin_token),
        )

        # List tags
        list_resp = tags_client.get(
            _tags_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert list_resp.status_code == 200
        items = list_resp.get_json()["items"]
        assert not any(item["id"] == tag_id for item in items)

    def test_404_delete_non_existent_tag(self, tags_client, admin_token, tags_app):
        """Delete non-existent tag returns 404."""
        fake_tag_id = str(uuid4())
        resp = tags_client.delete(
            _tag_url(tags_app._test_project_1_id, fake_tag_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_403_non_member_cannot_delete(self, tags_client, admin_token, outsider_token, tags_app):
        """Non-member cannot delete tags."""
        # Create tag as admin
        create_resp = tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(),
            headers=_auth(admin_token),
        )
        tag_id = create_resp.get_json()["id"]

        # Try to delete as outsider
        resp = tags_client.delete(
            _tag_url(tags_app._test_project_1_id, tag_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403


# ===========================================================================
# GET /api/v1/projects/<project_id>/tag-summary — cost rollup by tag
# ===========================================================================


class TestTagSummaryEndpoint:
    def test_200_empty_project_summary(self, tags_client, admin_token, tags_app):
        """Empty project returns empty summary (no rows)."""
        resp = tags_client.get(
            _summary_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["rows"] == []

    def test_200_summary_with_created_tags_no_activity(self, tags_client, admin_token, tags_app):
        """Tags with no labor/invoice activity still appear with zero costs."""
        # Create two tags but no labor/invoices
        tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="EmptyTag1"),
            headers=_auth(admin_token),
        )
        tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="EmptyTag2"),
            headers=_auth(admin_token),
        )

        resp = tags_client.get(
            _summary_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        for row in data["rows"]:
            assert row["labor_cost"] == 0
            assert row["expense_total"] == 0
            assert row["labor_entry_count"] == 0
            assert row["invoice_count"] == 0

    def test_200_summary_rows_sorted_by_name(self, tags_client, admin_token, tags_app):
        """Summary rows are sorted by tag name (untagged last)."""
        for name in ["Charpente", "Fondations", "Couverture"]:
            tags_client.post(
                _tags_url(tags_app._test_project_1_id),
                json=_valid_create_body(name=name),
                headers=_auth(admin_token),
            )

        resp = tags_client.get(
            _summary_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        tag_names = [row["tag_name"] for row in data["rows"]]
        # Should be sorted alphabetically
        assert tag_names == sorted(tag_names)

    def test_200_summary_response_shape(self, tags_client, admin_token, tags_app):
        """Summary row has required fields."""
        tags_client.post(
            _tags_url(tags_app._test_project_1_id),
            json=_valid_create_body(name="TestTag"),
            headers=_auth(admin_token),
        )

        resp = tags_client.get(
            _summary_url(tags_app._test_project_1_id),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        row = data["rows"][0]
        required_keys = {
            "tag_id",
            "tag_name",
            "tag_color",
            "labor_cost",
            "expense_total",
            "labor_entry_count",
            "invoice_count",
        }
        assert required_keys.issubset(row.keys())

    def test_401_unauthenticated(self, tags_client, tags_app):
        """Unauthenticated request returns 401."""
        resp = tags_client.get(_summary_url(tags_app._test_project_1_id))
        assert resp.status_code == 401

    def test_403_non_member(self, tags_client, outsider_token, tags_app):
        """Non-member user gets 403 Forbidden."""
        resp = tags_client.get(
            _summary_url(tags_app._test_project_1_id),
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403
