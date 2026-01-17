"""
Dependency Injection Wiring

This module defines the dependency injection container pattern for the hexagonal architecture.
Ports (interfaces) are defined here and bound to infrastructure implementations.

The core domain should depend only on ports (abstractions), not on concrete implementations.
This follows the Dependency Inversion Principle.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


# =============================================================================
# PORTS (Interfaces)
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
        """
        Send an email.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body

        Returns:
            True if sent successfully, False otherwise
        """
        ...


class QueuePort(Protocol):
    """Port for task queue operations."""

    def enqueue(self, task_name: str, payload: Dict[str, Any]) -> str:
        """
        Enqueue a task for background processing.

        Args:
            task_name: Name of the task to execute
            payload: Task payload/arguments

        Returns:
            Job ID
        """
        ...


class ProjectRepository(Protocol):
    """Port for project persistence operations."""

    def find_all(self) -> list:
        """Find all projects."""
        ...

    def find_by_id(self, project_id: str) -> Optional[Any]:
        """Find a project by ID."""
        ...

    def save(self, project: Any) -> Any:
        """Save a project."""
        ...

    def delete(self, project_id: str) -> bool:
        """Delete a project by ID."""
        ...


class UserRepository(Protocol):
    """Port for user persistence operations."""

    def find_by_id(self, user_id: str) -> Optional[Any]:
        """Find a user by ID."""
        ...

    def find_by_email(self, email: str) -> Optional[Any]:
        """Find a user by email."""
        ...

    def save(self, user: Any) -> Any:
        """Save a user."""
        ...


# =============================================================================
# CONTAINER
# =============================================================================


@dataclass
class Container:
    """
    Dependency Injection Container.

    Holds references to all infrastructure implementations bound to their ports.
    In production, these would be actual implementations.
    Currently contains stubs that will be replaced with real implementations.
    """

    email_service: Optional[EmailPort] = None
    queue_service: Optional[QueuePort] = None
    project_repository: Optional[ProjectRepository] = None
    user_repository: Optional[UserRepository] = None


# Global container instance
# Will be configured at application startup
container = Container()


def configure_container(
    email_service: Optional[EmailPort] = None,
    queue_service: Optional[QueuePort] = None,
    project_repository: Optional[ProjectRepository] = None,
    user_repository: Optional[UserRepository] = None,
) -> Container:
    """
    Configure the dependency injection container.

    This should be called once at application startup to wire up
    infrastructure implementations to their ports.

    Args:
        email_service: Email service implementation
        queue_service: Queue service implementation
        project_repository: Project repository implementation
        user_repository: User repository implementation

    Returns:
        Configured container
    """
    global container
    container = Container(
        email_service=email_service,
        queue_service=queue_service,
        project_repository=project_repository,
        user_repository=user_repository,
    )
    return container


def get_container() -> Container:
    """Get the current container instance."""
    return container
