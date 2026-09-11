"""Tests for the authentication database models.

A user carries identity only: roles live on `user_company_access`, so there is
no `users` ↔ roles relationship and no `roles`/`permissions` table to assert.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import UserModel


class TestUserModel:
    """Test UserModel database operations."""

    def test_create_user(self, session):
        """Test creating a user with all required fields."""
        user = UserModel(
            email="test@example.com",
            is_active=True,
        )
        session.add(user)
        session.commit()

        assert user.id is not None
        assert user.email == "test@example.com"
        assert not hasattr(user, "password_hash")
        assert user.is_active is True
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_email_uniqueness_constraint(self, session):
        """Test that duplicate emails are rejected."""
        user1 = UserModel(email="unique@example.com")
        session.add(user1)
        session.commit()

        user2 = UserModel(email="unique@example.com")
        session.add(user2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_user_carries_no_roles(self, session):
        """A user row has no role relationship: roles are per company."""
        user = UserModel(email="norole@example.com")
        session.add(user)
        session.commit()

        assert not hasattr(user, "roles")


class TestDatabaseSchema:
    """Test overall database schema integrity."""

    def test_legacy_role_tables_are_gone(self, engine):
        """The legacy RBAC tables must not be recreated by the models."""
        from sqlalchemy import inspect

        table_names = inspect(engine).get_table_names()

        assert "users" in table_names
        assert "user_company_access" in table_names
        for dropped in ("roles", "permissions", "user_roles", "role_permissions"):
            assert dropped not in table_names

    def test_user_table_columns(self, engine):
        """Test users table has correct columns."""
        from sqlalchemy import inspect

        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("users")}

        assert "id" in columns
        assert "email" in columns
        assert "password_hash" not in columns
        assert "is_active" in columns
        assert "is_platform_ops" in columns
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

    def test_user_projects_carries_no_role(self, engine):
        """A project assignment references a user and a project, nothing else."""
        from sqlalchemy import inspect

        inspector = inspect(engine)

        columns = {col["name"] for col in inspector.get_columns("user_projects")}
        assert "role_id" not in columns

        fk_tables = {fk["referred_table"] for fk in inspector.get_foreign_keys("user_projects")}
        assert fk_tables == {"users", "projects"}

    def test_user_projects_cascades_from_both_parents(self, engine):
        """Deleting a user or a project removes the assignment with it."""
        from sqlalchemy import inspect

        for fk in inspect(engine).get_foreign_keys("user_projects"):
            expected = "SET NULL" if fk["constrained_columns"] == ["invited_by_user_id"] else "CASCADE"
            assert fk.get("options", {}).get("ondelete") == expected
