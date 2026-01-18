"""Unit tests for AuthorizationService domain service."""

import pytest
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from app.domain.services.authorization_service import AuthorizationService


class TestAuthorizationServiceGetUserPermissions:
    """Tests for AuthorizationService.get_user_permissions() method."""

    @pytest.fixture
    def mock_user_repo(self):
        """Create mock user repository."""
        return Mock()

    @pytest.fixture
    def authz_service(self, mock_user_repo):
        """Create AuthorizationService with mocked repository."""
        return AuthorizationService(mock_user_repo)

    @pytest.fixture
    def mock_user_with_roles(self):
        """Create mock user with roles and permissions."""
        user = MagicMock()
        user.id = uuid4()

        # Create mock roles with permissions
        admin_role = MagicMock()
        admin_role.name = "admin"
        perm1 = MagicMock()
        perm1.name = "project:create"
        perm2 = MagicMock()
        perm2.name = "project:delete"
        admin_role.permissions = [perm1, perm2]

        user_role = MagicMock()
        user_role.name = "user"
        perm3 = MagicMock()
        perm3.name = "project:read"
        user_role.permissions = [perm3]

        user.roles = [admin_role, user_role]
        return user

    def test_get_user_permissions_aggregates_all_roles(
        self, authz_service, mock_user_repo, mock_user_with_roles
    ):
        """Should return all permissions aggregated from all roles."""
        mock_user_repo.find_by_id.return_value = mock_user_with_roles

        permissions = authz_service.get_user_permissions(mock_user_with_roles.id)

        assert permissions == {"project:create", "project:delete", "project:read"}

    def test_get_user_permissions_user_not_found(self, authz_service, mock_user_repo):
        """Should return empty set when user not found."""
        mock_user_repo.find_by_id.return_value = None

        permissions = authz_service.get_user_permissions(uuid4())

        assert permissions == set()

    def test_get_user_permissions_no_roles(self, authz_service, mock_user_repo):
        """Should return empty set when user has no roles."""
        user = MagicMock()
        user.id = uuid4()
        user.roles = []
        mock_user_repo.find_by_id.return_value = user

        permissions = authz_service.get_user_permissions(user.id)

        assert permissions == set()

    def test_get_user_permissions_deduplicates(self, authz_service, mock_user_repo):
        """Should deduplicate permissions across roles."""
        user = MagicMock()
        user.id = uuid4()

        # Two roles with overlapping permission
        role1 = MagicMock()
        role1.name = "role1"
        perm1 = MagicMock()
        perm1.name = "project:read"
        role1.permissions = [perm1]

        role2 = MagicMock()
        role2.name = "role2"
        perm2 = MagicMock()
        perm2.name = "project:read"  # Same permission
        role2.permissions = [perm2]

        user.roles = [role1, role2]
        mock_user_repo.find_by_id.return_value = user

        permissions = authz_service.get_user_permissions(user.id)

        assert permissions == {"project:read"}


class TestAuthorizationServiceHasPermission:
    """Tests for AuthorizationService.has_permission() method."""

    @pytest.fixture
    def mock_user_repo(self):
        """Create mock user repository."""
        return Mock()

    @pytest.fixture
    def authz_service(self, mock_user_repo):
        """Create AuthorizationService."""
        return AuthorizationService(mock_user_repo)

    @pytest.fixture
    def user_with_perms(self, mock_user_repo):
        """Create user with project:read and project:write permissions."""
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "editor"
        perm1 = MagicMock()
        perm1.name = "project:read"
        perm2 = MagicMock()
        perm2.name = "project:write"
        role.permissions = [perm1, perm2]
        user.roles = [role]
        mock_user_repo.find_by_id.return_value = user
        return user

    def test_has_permission_true(self, authz_service, user_with_perms):
        """Should return True when user has exact permission."""
        result = authz_service.has_permission(user_with_perms.id, "project:read")
        assert result is True

    def test_has_permission_false(self, authz_service, user_with_perms):
        """Should return False when user lacks permission."""
        result = authz_service.has_permission(user_with_perms.id, "user:delete")
        assert result is False

    def test_has_permission_wildcard_all(self, authz_service, mock_user_repo):
        """Should return True for *:* (superuser) permission."""
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "superuser"
        perm = MagicMock()
        perm.name = "*:*"
        role.permissions = [perm]
        user.roles = [role]
        mock_user_repo.find_by_id.return_value = user

        result = authz_service.has_permission(user.id, "anything:here")
        assert result is True

    def test_has_permission_resource_wildcard(self, authz_service, mock_user_repo):
        """Should return True for resource:* wildcard permission."""
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "project_admin"
        perm = MagicMock()
        perm.name = "project:*"
        role.permissions = [perm]
        user.roles = [role]
        mock_user_repo.find_by_id.return_value = user

        assert authz_service.has_permission(user.id, "project:read") is True
        assert authz_service.has_permission(user.id, "project:delete") is True
        assert authz_service.has_permission(user.id, "user:read") is False


class TestAuthorizationServiceHasAnyPermission:
    """Tests for AuthorizationService.has_any_permission() method."""

    @pytest.fixture
    def authz_service(self):
        """Create AuthorizationService."""
        mock_repo = Mock()
        return AuthorizationService(mock_repo), mock_repo

    def test_has_any_permission_true(self, authz_service):
        """Should return True when user has at least one permission."""
        service, mock_repo = authz_service
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "viewer"
        perm = MagicMock()
        perm.name = "project:read"
        role.permissions = [perm]
        user.roles = [role]
        mock_repo.find_by_id.return_value = user

        result = service.has_any_permission(
            user.id, ["project:read", "project:write", "user:delete"]
        )
        assert result is True

    def test_has_any_permission_false(self, authz_service):
        """Should return False when user has none of the permissions."""
        service, mock_repo = authz_service
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "viewer"
        perm = MagicMock()
        perm.name = "project:read"
        role.permissions = [perm]
        user.roles = [role]
        mock_repo.find_by_id.return_value = user

        result = service.has_any_permission(user.id, ["user:delete", "admin:manage"])
        assert result is False


class TestAuthorizationServiceHasAllPermissions:
    """Tests for AuthorizationService.has_all_permissions() method."""

    @pytest.fixture
    def authz_service(self):
        """Create AuthorizationService."""
        mock_repo = Mock()
        return AuthorizationService(mock_repo), mock_repo

    def test_has_all_permissions_true(self, authz_service):
        """Should return True when user has all permissions."""
        service, mock_repo = authz_service
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "editor"
        perm1 = MagicMock()
        perm1.name = "project:read"
        perm2 = MagicMock()
        perm2.name = "project:write"
        role.permissions = [perm1, perm2]
        user.roles = [role]
        mock_repo.find_by_id.return_value = user

        result = service.has_all_permissions(
            user.id, ["project:read", "project:write"]
        )
        assert result is True

    def test_has_all_permissions_false(self, authz_service):
        """Should return False when user lacks at least one permission."""
        service, mock_repo = authz_service
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "editor"
        perm = MagicMock()
        perm.name = "project:read"
        role.permissions = [perm]
        user.roles = [role]
        mock_repo.find_by_id.return_value = user

        result = service.has_all_permissions(
            user.id, ["project:read", "project:write"]
        )
        assert result is False


class TestAuthorizationServiceHasRole:
    """Tests for AuthorizationService.has_role() method."""

    @pytest.fixture
    def authz_service(self):
        """Create AuthorizationService."""
        mock_repo = Mock()
        return AuthorizationService(mock_repo), mock_repo

    @pytest.fixture
    def user_with_admin_role(self, authz_service):
        """Create user with admin role."""
        _, mock_repo = authz_service
        user = MagicMock()
        user.id = uuid4()
        role = MagicMock()
        role.name = "admin"
        role.permissions = []
        user.roles = [role]
        mock_repo.find_by_id.return_value = user
        return user

    def test_has_role_true(self, authz_service, user_with_admin_role):
        """Should return True when user has role."""
        service, _ = authz_service
        result = service.has_role(user_with_admin_role.id, "admin")
        assert result is True

    def test_has_role_false(self, authz_service, user_with_admin_role):
        """Should return False when user lacks role."""
        service, _ = authz_service
        result = service.has_role(user_with_admin_role.id, "superadmin")
        assert result is False

    def test_has_role_case_insensitive(self, authz_service, user_with_admin_role):
        """Should match role names case-insensitively."""
        service, _ = authz_service
        result = service.has_role(user_with_admin_role.id, "ADMIN")
        assert result is True

    def test_has_role_user_not_found(self, authz_service):
        """Should return False when user not found."""
        service, mock_repo = authz_service
        mock_repo.find_by_id.return_value = None

        result = service.has_role(uuid4(), "admin")
        assert result is False
