"""
Dependency Injection Wiring

This module defines the dependency injection container pattern for the hexagonal architecture.
Ports (interfaces) are defined here and bound to infrastructure implementations.

The core domain should depend only on ports (abstractions), not on concrete implementations.
This follows the Dependency Inversion Principle.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

# Import port interfaces from application layer
from app.application.ports.password_hasher_port import PasswordHasherPort
from app.application.ports.token_issuer_port import TokenIssuerPort
from app.application.ports.session_manager_port import SessionManagerPort
from app.application.ports.user_repository_port import UserRepositoryPort

# Import domain services
from app.domain.services.auth_service import AuthService
from app.domain.services.authorization_service import AuthorizationService


# =============================================================================
# PORTS (Interfaces) - Legacy ports kept for compatibility
# =============================================================================


class EmailPort(Protocol):
    """Port for sending emails."""

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
    ) -> bool:
        ...


class QueuePort(Protocol):
    """Port for task queue operations."""

    def enqueue(self, task_name: str, payload: Dict[str, Any]) -> str:
        ...


class ProjectRepository(Protocol):
    """Port for project persistence operations."""

    def find_all(self) -> list:
        ...

    def find_by_id(self, project_id: str) -> Optional[Any]:
        ...

    def save(self, project: Any) -> Any:
        ...

    def delete(self, project_id: str) -> bool:
        ...


# =============================================================================
# CONTAINER
# =============================================================================


@dataclass
class Container:
    """
    Dependency Injection Container.

    Holds references to all infrastructure implementations bound to their ports.
    """

    # Infrastructure ports
    email_service: Optional[EmailPort] = None
    queue_service: Optional[QueuePort] = None
    project_repository: Optional[ProjectRepository] = None

    # Auth ports
    user_repository: Optional[UserRepositoryPort] = None
    password_hasher: Optional[PasswordHasherPort] = None
    token_issuer: Optional[TokenIssuerPort] = None
    session_manager: Optional[SessionManagerPort] = None

    # Domain services (configured after ports)
    auth_service: Optional[AuthService] = None
    authorization_service: Optional[AuthorizationService] = None


# Global container instance
container = Container()


def configure_container(
    email_service: Optional[EmailPort] = None,
    queue_service: Optional[QueuePort] = None,
    project_repository: Optional[ProjectRepository] = None,
    user_repository: Optional[UserRepositoryPort] = None,
    password_hasher: Optional[PasswordHasherPort] = None,
    token_issuer: Optional[TokenIssuerPort] = None,
    session_manager: Optional[SessionManagerPort] = None,
) -> Container:
    """
    Configure the dependency injection container.

    This should be called once at application startup to wire up
    infrastructure implementations to their ports.
    """
    global container

    container = Container(
        email_service=email_service,
        queue_service=queue_service,
        project_repository=project_repository,
        user_repository=user_repository,
        password_hasher=password_hasher,
        token_issuer=token_issuer,
        session_manager=session_manager,
    )

    # Wire up domain services if repositories are provided
    if user_repository and password_hasher:
        container.auth_service = AuthService(user_repository, password_hasher)
    if user_repository:
        container.authorization_service = AuthorizationService(user_repository)

    return container


def get_container() -> Container:
    """Get the current container instance."""
    return container
