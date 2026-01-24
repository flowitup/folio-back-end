"""Login use case."""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from app.application.ports.token_issuer import TokenIssuerPort
from app.domain.services.auth import AuthService
from app.domain.services.authorization import AuthorizationService


@dataclass
class LoginResult:
    """Result of successful login."""
    user_id: UUID
    access_token: str
    refresh_token: str
    permissions: List[str]


class LoginUseCase:
    """Application use case for user login."""

    def __init__(
        self,
        auth_service: AuthService,
        authorization_service: AuthorizationService,
        token_issuer: TokenIssuerPort,
    ):
        self._auth = auth_service
        self._authz = authorization_service
        self._tokens = token_issuer

    def execute(self, email: str, password: str) -> LoginResult:
        """
        Execute login flow.

        1. Authenticate credentials
        2. Get user permissions
        3. Generate tokens

        Args:
            email: User email
            password: Plaintext password

        Returns:
            LoginResult with tokens and permissions

        Raises:
            AuthenticationError: If credentials invalid
        """
        user_id = self._auth.authenticate(email, password)
        permissions = list(self._authz.get_user_permissions(user_id))

        access_token = self._tokens.create_access_token(
            user_id,
            {"permissions": permissions},
        )
        refresh_token = self._tokens.create_refresh_token(user_id)

        return LoginResult(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            permissions=permissions,
        )
