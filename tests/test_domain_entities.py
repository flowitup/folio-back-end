"""Tests for domain entities.

`Role` and `Permission` are gone: a user carries identity only, and what they
may do is resolved per request from their company role plus grant/deny rows
(see tests/unit/test_authz_matrix.py and tests/api/test_authz_*).
"""

import pytest
from uuid import UUID, uuid4
from datetime import datetime

from app.domain.entities.user import User, InvalidEmailError


class TestUserEntity:
    """Test User domain entity."""

    def test_create_user(self):
        """Test creating a user using factory method."""
        user = User.create(email="Test@Example.com", password_hash="hashed_password")

        assert isinstance(user.id, UUID)
        assert user.email == "test@example.com"  # Should be lowercased and stripped
        assert user.password_hash == "hashed_password"
        assert user.is_active is True
        assert isinstance(user.created_at, datetime)
        assert isinstance(user.updated_at, datetime)

    def test_email_normalization(self):
        """Test that email is normalized (lowercased and stripped)."""
        user = User.create(email="  UPPER@CASE.COM  ", password_hash="hash")

        assert user.email == "upper@case.com"

    def test_user_carries_no_roles_or_permissions(self):
        """Identity only: nothing on the entity answers "may I?"."""
        user = User.create(email="user@example.com", password_hash="hash")

        assert not hasattr(user, "roles")
        assert not hasattr(user, "has_permission")
        assert not hasattr(user, "has_role")

    def test_platform_ops_defaults_to_false(self):
        """The support bypass is opt-in, never granted by creating an account."""
        user = User.create(email="user@example.com", password_hash="hash")

        assert user.is_platform_ops is False

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
        shared_id = uuid4()
        user1 = User(id=shared_id, email="a@example.com", password_hash="hash")
        user2 = User(id=shared_id, email="b@example.com", password_hash="hash")
        user3 = User(id=uuid4(), email="a@example.com", password_hash="hash")

        assert user1 == user2  # Same ID
        assert user1 != user3  # Different ID

    def test_entity_hashable(self):
        """Test that entities can be used in sets."""
        shared_id = uuid4()
        user1 = User(id=shared_id, email="a@example.com", password_hash="hash")
        user2 = User(id=shared_id, email="b@example.com", password_hash="hash")

        assert len({user1, user2}) == 1  # Same ID means same hash
