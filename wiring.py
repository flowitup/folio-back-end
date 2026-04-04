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
from app.application.ports.password_hasher import PasswordHasherPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.application.ports.session_manager import SessionManagerPort
from app.application.ports.user_repository import UserRepositoryPort

# Import domain services
from app.domain.services.auth import AuthService
from app.domain.services.authorization import AuthorizationService

# Import use cases
from app.application.usecases import LoginUseCase, LogoutUseCase
from app.application.projects import (
    IProjectRepository,
    CreateProjectUseCase,
    ListProjectsUseCase,
    GetProjectUseCase,
    UpdateProjectUseCase,
    DeleteProjectUseCase,
)
from app.application.labor import (
    IWorkerRepository,
    ILaborEntryRepository,
    CreateWorkerUseCase,
    UpdateWorkerUseCase,
    DeleteWorkerUseCase,
    ListWorkersUseCase,
    LogAttendanceUseCase,
    UpdateAttendanceUseCase,
    DeleteAttendanceUseCase,
    ListLaborEntriesUseCase,
    GetLaborSummaryUseCase,
)


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

    # Labor ports
    worker_repository: Optional[IWorkerRepository] = None
    labor_entry_repository: Optional[ILaborEntryRepository] = None

    # Domain services (configured after ports)
    auth_service: Optional[AuthService] = None
    authorization_service: Optional[AuthorizationService] = None

    # Use cases (configured after domain services)
    login_usecase: Optional[LoginUseCase] = None
    logout_usecase: Optional[LogoutUseCase] = None

    # Project use cases
    create_project_usecase: Optional[CreateProjectUseCase] = None
    list_projects_usecase: Optional[ListProjectsUseCase] = None
    get_project_usecase: Optional[GetProjectUseCase] = None
    update_project_usecase: Optional[UpdateProjectUseCase] = None
    delete_project_usecase: Optional[DeleteProjectUseCase] = None

    # Labor use cases
    create_worker_usecase: Optional[CreateWorkerUseCase] = None
    update_worker_usecase: Optional[UpdateWorkerUseCase] = None
    delete_worker_usecase: Optional[DeleteWorkerUseCase] = None
    list_workers_usecase: Optional[ListWorkersUseCase] = None
    log_attendance_usecase: Optional[LogAttendanceUseCase] = None
    update_attendance_usecase: Optional[UpdateAttendanceUseCase] = None
    delete_attendance_usecase: Optional[DeleteAttendanceUseCase] = None
    list_labor_entries_usecase: Optional[ListLaborEntriesUseCase] = None
    get_labor_summary_usecase: Optional[GetLaborSummaryUseCase] = None


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
    worker_repository: Optional[IWorkerRepository] = None,
    labor_entry_repository: Optional[ILaborEntryRepository] = None,
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
        worker_repository=worker_repository,
        labor_entry_repository=labor_entry_repository,
    )

    # Wire up domain services if repositories are provided
    if user_repository and password_hasher:
        container.auth_service = AuthService(user_repository, password_hasher)
    if user_repository:
        container.authorization_service = AuthorizationService(user_repository)

    # Wire up use cases if dependencies are available
    if container.auth_service and container.authorization_service and token_issuer:
        container.login_usecase = LoginUseCase(
            container.auth_service,
            container.authorization_service,
            token_issuer,
        )
    if token_issuer:
        container.logout_usecase = LogoutUseCase(token_issuer)

    # Wire up project use cases if repository is available
    if project_repository:
        container.create_project_usecase = CreateProjectUseCase(project_repository)
        container.list_projects_usecase = ListProjectsUseCase(project_repository)
        container.get_project_usecase = GetProjectUseCase(project_repository)
        container.update_project_usecase = UpdateProjectUseCase(project_repository)
        container.delete_project_usecase = DeleteProjectUseCase(project_repository)

    # Wire up labor use cases if repositories are available
    if worker_repository:
        container.create_worker_usecase = CreateWorkerUseCase(worker_repository)
        container.update_worker_usecase = UpdateWorkerUseCase(worker_repository)
        container.delete_worker_usecase = DeleteWorkerUseCase(worker_repository)
        container.list_workers_usecase = ListWorkersUseCase(worker_repository)

    if worker_repository and labor_entry_repository:
        container.log_attendance_usecase = LogAttendanceUseCase(worker_repository, labor_entry_repository)
        container.list_labor_entries_usecase = ListLaborEntriesUseCase(worker_repository, labor_entry_repository)

    if labor_entry_repository:
        container.update_attendance_usecase = UpdateAttendanceUseCase(labor_entry_repository)
        container.delete_attendance_usecase = DeleteAttendanceUseCase(labor_entry_repository)
        container.get_labor_summary_usecase = GetLaborSummaryUseCase(labor_entry_repository)

    return container


def get_container() -> Container:
    """Get the current container instance."""
    return container
