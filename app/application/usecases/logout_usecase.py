"""Logout use case."""

from app.application.ports.token_issuer_port import TokenIssuerPort


class LogoutUseCase:
    """Application use case for user logout."""

    def __init__(self, token_issuer: TokenIssuerPort):
        self._tokens = token_issuer

    def execute(self, jti: str) -> None:
        """
        Execute logout by revoking token.

        Args:
            jti: JWT ID to revoke
        """
        self._tokens.revoke_token(jti)
