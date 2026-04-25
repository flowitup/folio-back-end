"""Unit tests for AuthService domain service."""

import pytest
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from app.domain.services.auth import AuthService
from app.domain.exceptions.auth_exceptions import (
    InvalidCredentialsError,
    UserInactiveError,
)


class TestAuthService:
    """Tests for AuthService.authenticate() method."""

    @pytest.fixture
    def mock_user_repo(self):
        """Create mock user repository."""
        return Mock()

    @pytest.fixture
    def mock_hasher(self):
        """Create mock password hasher."""
        hasher = Mock()
        hasher.verify.return_value = True
        hasher.hash.return_value = "hashed"
        return hasher

    @pytest.fixture
    def auth_service(self, mock_user_repo, mock_hasher):
        """Create AuthService with mocked dependencies."""
        return AuthService(mock_user_repo, mock_hasher)

    @pytest.fixture
    def mock_user(self):
        """Create mock user entity."""
        user = MagicMock()
        user.id = uuid4()
        user.email = "test@example.com"
        user.password_hash = "hashed_password"
        user.is_active = True
        return user

    def test_authenticate_success(self, auth_service, mock_user_repo, mock_hasher, mock_user):
        """Should return user ID for valid credentials."""
        mock_user_repo.find_by_email.return_value = mock_user

        result = auth_service.authenticate("test@example.com", "password123")

        assert result == mock_user.id
        mock_user_repo.find_by_email.assert_called_once_with("test@example.com")
        mock_hasher.verify.assert_called_once_with("password123", mock_user.password_hash)

    def test_authenticate_user_not_found(self, auth_service, mock_user_repo, mock_hasher):
        """Should raise InvalidCredentialsError for unknown email (generic for security)."""
        mock_user_repo.find_by_email.return_value = None

        with pytest.raises(InvalidCredentialsError) as exc_info:
            auth_service.authenticate("unknown@example.com", "password123")

        assert "Invalid email or password" in str(exc_info.value)
        # Verify timing-attack prevention (dummy hash called)
        mock_hasher.hash.assert_called_once_with("dummy_password")

    def test_authenticate_inactive_user(self, auth_service, mock_user_repo, mock_user):
        """Should raise UserInactiveError for deactivated account."""
        mock_user.is_active = False
        mock_user_repo.find_by_email.return_value = mock_user

        with pytest.raises(UserInactiveError) as exc_info:
            auth_service.authenticate("test@example.com", "password123")

        assert "deactivated" in str(exc_info.value).lower()

    def test_authenticate_invalid_password(self, auth_service, mock_user_repo, mock_hasher, mock_user):
        """Should raise InvalidCredentialsError for wrong password."""
        mock_user_repo.find_by_email.return_value = mock_user
        mock_hasher.verify.return_value = False

        with pytest.raises(InvalidCredentialsError) as exc_info:
            auth_service.authenticate("test@example.com", "wrong_password")

        assert "Invalid email or password" in str(exc_info.value)

    def test_authenticate_normalizes_email_lowercase(self, auth_service, mock_user_repo, mock_hasher, mock_user):
        """Should lowercase email before lookup."""
        mock_user_repo.find_by_email.return_value = mock_user

        auth_service.authenticate("TEST@EXAMPLE.COM", "password123")

        mock_user_repo.find_by_email.assert_called_once_with("test@example.com")

    def test_authenticate_normalizes_email_strips_whitespace(
        self, auth_service, mock_user_repo, mock_hasher, mock_user
    ):
        """Should strip whitespace from email before lookup."""
        mock_user_repo.find_by_email.return_value = mock_user

        auth_service.authenticate("  test@example.com  ", "password123")

        mock_user_repo.find_by_email.assert_called_once_with("test@example.com")


class TestAuthServiceHashPassword:
    """Tests for AuthService.hash_password() method."""

    @pytest.fixture
    def mock_hasher(self):
        """Create mock password hasher."""
        hasher = Mock()
        hasher.hash.return_value = "$argon2id$v=19$m=65536,t=2,p=1$hashed"
        return hasher

    @pytest.fixture
    def auth_service(self, mock_hasher):
        """Create AuthService with mocked hasher."""
        mock_repo = Mock()
        return AuthService(mock_repo, mock_hasher)

    def test_hash_password_delegates_to_hasher(self, auth_service, mock_hasher):
        """Should delegate password hashing to hasher adapter."""
        result = auth_service.hash_password("my_password")

        mock_hasher.hash.assert_called_once_with("my_password")
        assert result == "$argon2id$v=19$m=65536,t=2,p=1$hashed"

    def test_hash_password_returns_hashed_string(self, auth_service):
        """Should return a hashed password string."""
        result = auth_service.hash_password("password123")

        assert isinstance(result, str)
        assert len(result) > 0
