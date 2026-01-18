"""Tests for domain entities."""

import pytest
from uuid import UUID
from datetime import datetime

from app.domain.entities.user import User, InvalidEmailError
from app.domain.entities.role import Role
from app.domain.entities.permission import Permission


class TestPermissionEntity:
    """Test Permission domain entity."""

    def test_create_permission(self):
        """Test creating a permission using factory method."""
        perm = Permission.create(resource="project", action="create")

        assert isinstance(perm.id, UUID)
        assert perm.name == "project:create"
        assert perm.resource == "project"
        assert perm.action == "create"
        assert isinstance(perm.created_at, datetime)

    def test_permission_matches_exact(self):
        """Test exact permission matching."""
        perm = Permission.create(resource="project", action="read")

        assert perm.matches("project", "read") is True
        assert perm.matches("project", "write") is False
        assert perm.matches("user", "read") is False

    def test_permission_wildcard_resource(self):
        """Test wildcard resource matching."""
        perm = Permission(
            id=UUID('12345678-1234-5678-1234-567812345678'),
            name="*:read",
            resource="*",
            action="read",
        )

        assert perm.matches("project", "read") is True
        assert perm.matches("user", "read") is True
        assert perm.matches("anything", "read") is True
        assert perm.matches("project", "write") is False

    def test_permission_wildcard_action(self):
        """Test wildcard action matching."""
        perm = Permission(
            id=UUID('12345678-1234-5678-1234-567812345678'),
            name="project:*",
            resource="project",
            action="*",
        )

        assert perm.matches("project", "read") is True
        assert perm.matches("project", "write") is True
        assert perm.matches("project", "delete") is True
        assert perm.matches("user", "read") is False

    def test_permission_full_wildcard(self):
        """Test full wildcard (superuser) permission."""
        perm = Permission(
            id=UUID('12345678-1234-5678-1234-567812345678'),
            name="*:*",
            resource="*",
            action="*",
        )

        assert perm.matches("project", "read") is True
        assert perm.matches("user", "delete") is True
        assert perm.matches("anything", "anything") is True


class TestRoleEntity:
    """Test Role domain entity."""

    def test_create_role(self):
        """Test creating a role using factory method."""
        role = Role.create(name="Admin", description="Administrator role")

        assert isinstance(role.id, UUID)
        assert role.name == "admin"  # Should be lowercased
        assert role.description == "Administrator role"
        assert isinstance(role.created_at, datetime)
        assert len(role.permissions) == 0

    def test_add_permission_to_role(self):
        """Test adding permissions to a role."""
        role = Role.create(name="editor")
        perm1 = Permission.create(resource="project", action="read")
        perm2 = Permission.create(resource="project", action="update")

        role.add_permission(perm1)
        role.add_permission(perm2)

        assert len(role.permissions) == 2
        assert perm1 in role.permissions
        assert perm2 in role.permissions

    def test_add_duplicate_permission(self):
        """Test that duplicate permissions are not added."""
        role = Role.create(name="editor")
        perm = Permission.create(resource="project", action="read")

        role.add_permission(perm)
        role.add_permission(perm)  # Try to add again

        assert len(role.permissions) == 1

    def test_has_permission(self):
        """Test role permission checking."""
        role = Role.create(name="editor")
        perm1 = Permission.create(resource="project", action="read")
        perm2 = Permission.create(resource="project", action="update")

        role.add_permission(perm1)
        role.add_permission(perm2)

        assert role.has_permission("project", "read") is True
        assert role.has_permission("project", "update") is True
        assert role.has_permission("project", "delete") is False
        assert role.has_permission("user", "read") is False


class TestUserEntity:
    """Test User domain entity."""

    def test_create_user(self):
        """Test creating a user using factory method."""
        user = User.create(
            email="Test@Example.com",
            password_hash="hashed_password"
        )

        assert isinstance(user.id, UUID)
        assert user.email == "test@example.com"  # Should be lowercased and stripped
        assert user.password_hash == "hashed_password"
        assert user.is_active is True
        assert isinstance(user.created_at, datetime)
        assert isinstance(user.updated_at, datetime)
        assert len(user.roles) == 0

    def test_email_normalization(self):
        """Test that email is normalized (lowercased and stripped)."""
        user = User.create(
            email="  UPPER@CASE.COM  ",
            password_hash="hash"
        )

        assert user.email == "upper@case.com"

    def test_add_role_to_user(self):
        """Test adding roles to a user."""
        user = User.create(email="user@example.com", password_hash="hash")
        role1 = Role.create(name="admin")
        role2 = Role.create(name="user")

        user.add_role(role1)
        user.add_role(role2)

        assert len(user.roles) == 2
        assert role1 in user.roles
        assert role2 in user.roles

    def test_add_duplicate_role(self):
        """Test that duplicate roles are not added."""
        user = User.create(email="user@example.com", password_hash="hash")
        role = Role.create(name="admin")

        user.add_role(role)
        user.add_role(role)  # Try to add again

        assert len(user.roles) == 1

    def test_remove_role(self):
        """Test removing a role from a user."""
        user = User.create(email="user@example.com", password_hash="hash")
        role = Role.create(name="admin")

        user.add_role(role)
        assert len(user.roles) == 1

        user.remove_role(role)
        assert len(user.roles) == 0

    def test_has_role(self):
        """Test checking if user has a specific role."""
        user = User.create(email="user@example.com", password_hash="hash")
        admin_role = Role.create(name="admin")
        user_role = Role.create(name="user")

        user.add_role(admin_role)
        user.add_role(user_role)

        assert user.has_role("admin") is True
        assert user.has_role("user") is True
        assert user.has_role("manager") is False

    def test_has_role_case_insensitive(self):
        """Test that role checking is case-insensitive."""
        user = User.create(email="user@example.com", password_hash="hash")
        role = Role.create(name="admin")

        user.add_role(role)

        assert user.has_role("Admin") is True
        assert user.has_role("ADMIN") is True
        assert user.has_role("admin") is True

    def test_has_permission_through_role(self):
        """Test that user inherits permissions from roles."""
        user = User.create(email="user@example.com", password_hash="hash")
        role = Role.create(name="editor")
        perm = Permission.create(resource="project", action="update")

        role.add_permission(perm)
        user.add_role(role)

        assert user.has_permission("project", "update") is True
        assert user.has_permission("project", "delete") is False

    def test_has_permission_from_multiple_roles(self):
        """Test that user can have permissions from multiple roles."""
        user = User.create(email="user@example.com", password_hash="hash")

        viewer_role = Role.create(name="viewer")
        viewer_perm = Permission.create(resource="project", action="read")
        viewer_role.add_permission(viewer_perm)

        editor_role = Role.create(name="editor")
        editor_perm = Permission.create(resource="project", action="update")
        editor_role.add_permission(editor_perm)

        user.add_role(viewer_role)
        user.add_role(editor_role)

        assert user.has_permission("project", "read") is True
        assert user.has_permission("project", "update") is True
        assert user.has_permission("project", "delete") is False

    def test_invalid_email_raises_error(self):
        """Test that invalid email format raises InvalidEmailError."""
        with pytest.raises(InvalidEmailError):
            User.create(email="not-an-email", password_hash="hash")

        with pytest.raises(InvalidEmailError):
            User.create(email="missing@domain", password_hash="hash")

        with pytest.raises(InvalidEmailError):
            User.create(email="@nodomain.com", password_hash="hash")

    def test_valid_email_formats(self):
        """Test that valid email formats are accepted."""
        # Standard email
        user1 = User.create(email="user@example.com", password_hash="hash")
        assert user1.email == "user@example.com"

        # Email with subdomain
        user2 = User.create(email="user@mail.example.com", password_hash="hash")
        assert user2.email == "user@mail.example.com"

        # Email with plus sign
        user3 = User.create(email="user+tag@example.com", password_hash="hash")
        assert user3.email == "user+tag@example.com"

    def test_entity_equality_by_id(self):
        """Test that entities are equal based on ID."""
        from uuid import uuid4

        id1 = uuid4()
        perm1 = Permission(id=id1, name="p:a", resource="p", action="a")
        perm2 = Permission(id=id1, name="p:a", resource="p", action="a")
        perm3 = Permission(id=uuid4(), name="p:a", resource="p", action="a")

        assert perm1 == perm2  # Same ID
        assert perm1 != perm3  # Different ID

    def test_entity_hashable(self):
        """Test that entities can be used in sets."""
        from uuid import uuid4

        id1 = uuid4()
        perm1 = Permission(id=id1, name="p:a", resource="p", action="a")
        perm2 = Permission(id=id1, name="p:a", resource="p", action="a")

        perm_set = {perm1, perm2}
        assert len(perm_set) == 1  # Same ID means same hash
