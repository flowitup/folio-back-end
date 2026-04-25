"""Tests for authentication database models."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import (
    UserModel,
    RoleModel,
    PermissionModel,
    user_roles,
    role_permissions,
)


class TestUserModel:
    """Test UserModel database operations."""

    def test_create_user(self, session):
        """Test creating a user with all required fields."""
        user = UserModel(
            email="test@example.com",
            password_hash="hashed_password_123",
            is_active=True,
        )
        session.add(user)
        session.commit()

        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.password_hash == "hashed_password_123"
        assert user.is_active is True
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_email_uniqueness_constraint(self, session):
        """Test that duplicate emails are rejected."""
        user1 = UserModel(email="unique@example.com", password_hash="hash1")
        session.add(user1)
        session.commit()

        user2 = UserModel(email="unique@example.com", password_hash="hash2")
        session.add(user2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_user_role_relationship(self, session):
        """Test many-to-many relationship between users and roles."""
        user = UserModel(email="user@example.com", password_hash="hash")
        role1 = RoleModel(name="admin", description="Admin role")
        role2 = RoleModel(name="user", description="User role")

        session.add_all([user, role1, role2])
        session.commit()

        user.roles.append(role1)
        user.roles.append(role2)
        session.commit()

        assert len(user.roles) == 2
        assert role1 in user.roles
        assert role2 in user.roles
        assert user in role1.users
        assert user in role2.users

    def test_user_role_cascade_delete(self, session):
        """Test cascade delete when user is deleted."""
        user = UserModel(email="cascade@example.com", password_hash="hash")
        role = RoleModel(name="test_role")

        session.add_all([user, role])
        session.commit()

        user.roles.append(role)
        session.commit()

        user_id = user.id
        role_id = role.id

        # Verify association exists
        result = session.execute(user_roles.select().where(user_roles.c.user_id == user_id)).first()
        assert result is not None

        # Delete user
        session.delete(user)
        session.commit()

        # Association should be deleted
        result = session.execute(user_roles.select().where(user_roles.c.user_id == user_id)).first()
        assert result is None

        # Role should still exist
        role_exists = session.get(RoleModel, role_id)
        assert role_exists is not None


class TestRoleModel:
    """Test RoleModel database operations."""

    def test_create_role(self, session):
        """Test creating a role with all fields."""
        role = RoleModel(name="manager", description="Project manager")
        session.add(role)
        session.commit()

        assert role.id is not None
        assert role.name == "manager"
        assert role.description == "Project manager"
        assert role.created_at is not None

    def test_role_name_uniqueness(self, session):
        """Test that role names must be unique."""
        role1 = RoleModel(name="admin")
        session.add(role1)
        session.commit()

        role2 = RoleModel(name="admin")
        session.add(role2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_role_permission_relationship(self, session):
        """Test many-to-many relationship between roles and permissions."""
        role = RoleModel(name="editor")
        perm1 = PermissionModel(name="project:create", resource="project", action="create")
        perm2 = PermissionModel(name="project:read", resource="project", action="read")

        session.add_all([role, perm1, perm2])
        session.commit()

        role.permissions.append(perm1)
        role.permissions.append(perm2)
        session.commit()

        assert len(role.permissions) == 2
        assert perm1 in role.permissions
        assert perm2 in role.permissions
        assert role in perm1.roles
        assert role in perm2.roles

    def test_role_cascade_delete(self, session):
        """Test cascade delete for role-permission association."""
        role = RoleModel(name="temp_role")
        perm = PermissionModel(name="test:action", resource="test", action="action")

        session.add_all([role, perm])
        session.commit()

        role.permissions.append(perm)
        session.commit()

        role_id = role.id
        perm_id = perm.id

        # Delete role
        session.delete(role)
        session.commit()

        # Association should be deleted
        result = session.execute(role_permissions.select().where(role_permissions.c.role_id == role_id)).first()
        assert result is None

        # Permission should still exist
        perm_exists = session.get(PermissionModel, perm_id)
        assert perm_exists is not None


class TestPermissionModel:
    """Test PermissionModel database operations."""

    def test_create_permission(self, session):
        """Test creating a permission."""
        perm = PermissionModel(name="user:update", resource="user", action="update")
        session.add(perm)
        session.commit()

        assert perm.id is not None
        assert perm.name == "user:update"
        assert perm.resource == "user"
        assert perm.action == "update"
        assert perm.created_at is not None

    def test_permission_name_uniqueness(self, session):
        """Test that permission names must be unique."""
        perm1 = PermissionModel(name="project:delete", resource="project", action="delete")
        session.add(perm1)
        session.commit()

        perm2 = PermissionModel(name="project:delete", resource="project", action="delete")
        session.add(perm2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_permission_index(self, session):
        """Test that resource-action index is created."""
        # This test verifies the index exists by checking table args
        from app.infrastructure.database.models import PermissionModel

        table_args = PermissionModel.__table_args__
        assert len(table_args) > 0

        # Check that the index exists
        index = table_args[0]
        assert index.name == "ix_permissions_resource_action"


class TestDatabaseSchema:
    """Test overall database schema integrity."""

    def test_all_tables_exist(self, engine):
        """Test that all expected tables are created."""
        from sqlalchemy import inspect

        inspector = inspect(engine)
        table_names = inspector.get_table_names()

        assert "users" in table_names
        assert "roles" in table_names
        assert "permissions" in table_names
        assert "user_roles" in table_names
        assert "role_permissions" in table_names

    def test_user_table_columns(self, engine):
        """Test users table has correct columns."""
        from sqlalchemy import inspect

        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("users")}

        assert "id" in columns
        assert "email" in columns
        assert "password_hash" in columns
        assert "is_active" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_user_table_constraints(self, engine):
        """Test users table has correct constraints."""
        from sqlalchemy import inspect

        inspector = inspect(engine)

        # Check unique constraints
        unique_constraints = inspector.get_unique_constraints("users")
        email_unique = any("email" in constraint.get("column_names", []) for constraint in unique_constraints)
        assert email_unique

    def test_association_table_foreign_keys(self, engine):
        """Test association tables have correct foreign keys."""
        from sqlalchemy import inspect

        inspector = inspect(engine)

        # Check user_roles foreign keys
        user_roles_fks = inspector.get_foreign_keys("user_roles")
        assert len(user_roles_fks) == 2

        fk_tables = {fk["referred_table"] for fk in user_roles_fks}
        assert "users" in fk_tables
        assert "roles" in fk_tables

        # Check role_permissions foreign keys
        role_perms_fks = inspector.get_foreign_keys("role_permissions")
        assert len(role_perms_fks) == 2

        fk_tables = {fk["referred_table"] for fk in role_perms_fks}
        assert "roles" in fk_tables
        assert "permissions" in fk_tables

    def test_cascade_delete_constraints(self, engine):
        """Test that CASCADE delete is configured on foreign keys."""
        from sqlalchemy import inspect

        inspector = inspect(engine)

        # Check user_roles cascades
        user_roles_fks = inspector.get_foreign_keys("user_roles")
        for fk in user_roles_fks:
            assert fk.get("options", {}).get("ondelete") == "CASCADE"

        # Check role_permissions cascades
        role_perms_fks = inspector.get_foreign_keys("role_permissions")
        for fk in role_perms_fks:
            assert fk.get("options", {}).get("ondelete") == "CASCADE"
