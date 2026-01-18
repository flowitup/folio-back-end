"""Authentication domain service."""

from uuid import UUID

from app.application.ports.password_hasher_port import PasswordHasherPort
from app.application.ports.user_repository_port import UserRepositoryPort
from app.domain.exceptions.auth_exceptions import (
    InvalidCredentialsError,
    UserInactiveError,
)


class AuthService:
    """Domain service for authentication logic."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        password_hasher: PasswordHasherPort,
    ):
        self._user_repo = user_repository
        self._hasher = password_hasher

    def authenticate(self, email: str, password: str) -> UUID:
        """
        Authenticate user with email/password.

        Args:
            email: User email
            password: Plaintext password

        Returns:
            User ID if successful

        Raises:
            InvalidCredentialsError: If credentials invalid (generic to prevent enumeration)
            UserInactiveError: User account is deactivated
        """
        user = self._user_repo.find_by_email(email.lower().strip())

        # Use generic error to prevent user enumeration
        if not user:
            # Still hash to prevent timing attacks
            self._hasher.hash("dummy_password")
            raise InvalidCredentialsError("Invalid email or password")

        if not user.is_active:
            raise UserInactiveError("User account is deactivated")

        if not self._hasher.verify(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")

        return user.id

    def hash_password(self, password: str) -> str:
        """Hash password for storage."""
        return self._hasher.hash(password)
