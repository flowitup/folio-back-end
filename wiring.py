"""
Dependency Injection Wiring

This module defines the dependency injection container pattern for the hexagonal architecture.
Ports (interfaces) are defined here and bound to infrastructure implementations.

The core domain should depend only on ports (abstractions), not on concrete implementations.
This follows the Dependency Inversion Principle.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

# Import port interfaces from application layer
from app.application.ports.email_port import EmailPort
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
from app.application.labor.export_labor_usecase import ExportLaborUseCase
from app.application.invoice.export_invoices_usecase import ExportInvoicesUseCase
from app.application.invoice import (
    IInvoiceRepository,
    CreateInvoiceUseCase,
    ListInvoicesUseCase,
    GetInvoiceUseCase,
    UpdateInvoiceUseCase,
    DeleteInvoiceUseCase,
    UploadAttachmentUseCase,
    ListAttachmentsUseCase,
    GetAttachmentUseCase,
    DeleteAttachmentUseCase,
)
from app.application.invoice.ports import IAttachmentStorage, IInvoiceAttachmentRepository
from app.application.task import (
    ITaskRepository,
    CreateTaskUseCase,
    ListTasksUseCase,
    GetTaskUseCase,
    UpdateTaskUseCase,
    MoveTaskUseCase,
    DeleteTaskUseCase,
)
from app.application.invitations import (
    CreateInvitationUseCase,
    VerifyInvitationUseCase,
    AcceptInvitationUseCase,
    RevokeInvitationUseCase,
    ListInvitationsUseCase,
)
from app.application.admin import BulkAddExistingUserUseCase
from app.application.notes.create_note_usecase import CreateNoteUseCase
from app.application.notes.list_project_notes_usecase import ListProjectNotesUseCase
from app.application.notes.update_note_usecase import UpdateNoteUseCase
from app.application.notes.delete_note_usecase import DeleteNoteUseCase
from app.application.notes.mark_note_done_usecase import MarkNoteDoneUseCase
from app.application.notes.mark_note_open_usecase import MarkNoteOpenUseCase
from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
from app.application.notes.dismiss_notification_usecase import DismissNotificationUseCase

# Companies use-cases (phase 03)
from app.application.companies import (
    CreateCompanyUseCase,
    UpdateCompanyUseCase,
    DeleteCompanyUseCase,
    ListAllCompaniesUseCase,
    GenerateInviteTokenUseCase,
    RevokeInviteTokenUseCase,
    ListAttachedUsersUseCase,
    BootAttachedUserUseCase,
    ListMyCompaniesUseCase,
    GetCompanyUseCase,
    RedeemInviteTokenUseCase,
    SetPrimaryCompanyUseCase,
    DetachCompanyUseCase,
)

# Billing use-cases (phase 04)
from app.application.billing import (
    CreateBillingDocumentUseCase,
    CloneBillingDocumentUseCase,
    ConvertDevisToFactureUseCase,
    UpdateBillingDocumentUseCase,
    UpdateBillingDocumentStatusUseCase,
    ListBillingDocumentsUseCase,
    GetBillingDocumentUseCase,
    DeleteBillingDocumentUseCase,
    RenderBillingDocumentPdfUseCase,
    CreateTemplateUseCase,
    UpdateTemplateUseCase,
    ListTemplatesUseCase,
    GetTemplateUseCase,
    DeleteTemplateUseCase,
    ApplyTemplateToCreateDocumentUseCase,
    GetCompanyProfileUseCase,
    UpsertCompanyProfileUseCase,
)

# =============================================================================
# PORTS (Interfaces)
# =============================================================================
# EmailPort is now imported from app.application.ports.email_port (single
# canonical contract: .send(EmailPayload)). The legacy send_email(to, subject,
# body, html_body) Protocol that used to live here had zero callers and was
# removed during the M3 unification.


class QueuePort(Protocol):
    """Port for task queue operations."""

    def enqueue(self, task_name: str, payload: Dict[str, Any]) -> str: ...


class ProjectRepository(Protocol):
    """Port for project persistence operations."""

    def find_all(self) -> list: ...

    def find_by_id(self, project_id: str) -> Optional[Any]: ...

    def save(self, project: Any) -> Any: ...

    def delete(self, project_id: str) -> bool: ...


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
    queue_service: Optional[QueuePort] = None
    project_repository: Optional[ProjectRepository] = None

    # Email — single canonical port (EmailPayload-based) + Jinja2 renderer.
    email_port: Optional[EmailPort] = None  # ResendEmailAdapter | InMemoryEmailAdapter
    email_renderer: Optional[Any] = None  # EmailRenderer

    # Auth ports
    user_repository: Optional[UserRepositoryPort] = None
    password_hasher: Optional[PasswordHasherPort] = None
    token_issuer: Optional[TokenIssuerPort] = None
    session_manager: Optional[SessionManagerPort] = None

    # Labor ports
    worker_repository: Optional[IWorkerRepository] = None
    labor_entry_repository: Optional[ILaborEntryRepository] = None

    # Invoice ports and use cases
    invoice_repository: Optional[IInvoiceRepository] = None
    create_invoice_usecase: Optional[CreateInvoiceUseCase] = None
    list_invoices_usecase: Optional[ListInvoicesUseCase] = None
    get_invoice_usecase: Optional[GetInvoiceUseCase] = None
    update_invoice_usecase: Optional[UpdateInvoiceUseCase] = None
    delete_invoice_usecase: Optional[DeleteInvoiceUseCase] = None

    # Attachment storage + repository
    attachment_storage: Optional[IAttachmentStorage] = None
    invoice_attachment_repository: Optional[IInvoiceAttachmentRepository] = None
    upload_attachment_usecase: Optional[UploadAttachmentUseCase] = None
    list_attachments_usecase: Optional[ListAttachmentsUseCase] = None
    get_attachment_usecase: Optional[GetAttachmentUseCase] = None
    delete_attachment_usecase: Optional[DeleteAttachmentUseCase] = None

    # Task (planning Kanban) ports + use cases
    task_repository: Optional[ITaskRepository] = None
    create_task_usecase: Optional[CreateTaskUseCase] = None
    list_tasks_usecase: Optional[ListTasksUseCase] = None
    get_task_usecase: Optional[GetTaskUseCase] = None
    update_task_usecase: Optional[UpdateTaskUseCase] = None
    move_task_usecase: Optional[MoveTaskUseCase] = None
    delete_task_usecase: Optional[DeleteTaskUseCase] = None

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

    # Invitation repos (bound in phase 05)
    invitation_repo: Optional[Any] = None  # SqlAlchemyInvitationRepository
    project_membership_repo: Optional[Any] = None  # SqlAlchemyProjectMembershipRepository
    role_repository: Optional[Any] = None  # SqlAlchemyRoleRepository (also used by roles API)

    # Invitation use cases (repos wired in phase 05)
    # app_base_url is read from APP_BASE_URL env var at configure_container time
    create_invitation_usecase: Optional[CreateInvitationUseCase] = None
    verify_invitation_usecase: Optional[VerifyInvitationUseCase] = None
    accept_invitation_usecase: Optional[AcceptInvitationUseCase] = None
    revoke_invitation_usecase: Optional[RevokeInvitationUseCase] = None
    list_invitations_usecase: Optional[ListInvitationsUseCase] = None

    # Admin use cases
    bulk_add_existing_user_usecase: Optional[BulkAddExistingUserUseCase] = None

    # Notes repos + use cases (phase 03)
    note_repo: Optional[Any] = None  # SqlAlchemyNoteRepository (NoteRepositoryPort + NoteQueryPort)
    note_dismissal_repo: Optional[Any] = None  # SqlAlchemyNoteDismissalRepository
    note_membership_reader: Optional[Any] = None  # SqlAlchemyProjectMembershipReader
    create_note_usecase: Optional[CreateNoteUseCase] = None
    list_project_notes_usecase: Optional[ListProjectNotesUseCase] = None
    update_note_usecase: Optional[UpdateNoteUseCase] = None
    delete_note_usecase: Optional[DeleteNoteUseCase] = None
    mark_note_done_usecase: Optional[MarkNoteDoneUseCase] = None
    mark_note_open_usecase: Optional[MarkNoteOpenUseCase] = None
    list_due_notifications_usecase: Optional[ListDueNotificationsUseCase] = None
    dismiss_notification_usecase: Optional[DismissNotificationUseCase] = None

    # Invoice export use case
    export_invoices_usecase: Optional[ExportInvoicesUseCase] = None

    # -----------------------------------------------------------------------
    # Companies repos + use-cases (phase 03)
    # -----------------------------------------------------------------------
    company_repo: Optional[Any] = None  # SqlAlchemyCompanyRepository
    user_company_access_repo: Optional[Any] = None  # SqlAlchemyUserCompanyAccessRepository
    company_invite_token_repo: Optional[Any] = None  # SqlAlchemyCompanyInviteTokenRepository

    # companies use-cases: admin
    create_company_usecase: Optional[CreateCompanyUseCase] = None
    update_company_usecase: Optional[UpdateCompanyUseCase] = None
    delete_company_usecase: Optional[DeleteCompanyUseCase] = None
    list_all_companies_usecase: Optional[ListAllCompaniesUseCase] = None
    generate_invite_token_usecase: Optional[GenerateInviteTokenUseCase] = None
    revoke_invite_token_usecase: Optional[RevokeInviteTokenUseCase] = None
    list_attached_users_usecase: Optional[ListAttachedUsersUseCase] = None
    boot_attached_user_usecase: Optional[BootAttachedUserUseCase] = None

    # companies use-cases: authenticated user
    list_my_companies_usecase: Optional[ListMyCompaniesUseCase] = None
    get_company_usecase: Optional[GetCompanyUseCase] = None
    redeem_invite_token_usecase: Optional[RedeemInviteTokenUseCase] = None
    set_primary_company_usecase: Optional[SetPrimaryCompanyUseCase] = None
    detach_company_usecase: Optional[DetachCompanyUseCase] = None

    # -----------------------------------------------------------------------
    # Billing repos + use-cases (phase 04)
    # -----------------------------------------------------------------------
    billing_document_repo: Optional[Any] = None  # SqlAlchemyBillingDocumentRepository
    billing_template_repo: Optional[Any] = None  # SqlAlchemyBillingTemplateRepository
    company_profile_repo: Optional[Any] = None  # SqlAlchemyCompanyProfileRepository
    billing_counter_repo: Optional[Any] = None  # SqlAlchemyBillingNumberCounterRepository
    billing_pdf_renderer: Optional[Any] = None  # ReportLabBillingDocumentPdfRenderer

    # billing-document use-cases
    create_billing_document_usecase: Optional[CreateBillingDocumentUseCase] = None
    clone_billing_document_usecase: Optional[CloneBillingDocumentUseCase] = None
    convert_devis_to_facture_usecase: Optional[ConvertDevisToFactureUseCase] = None
    update_billing_document_usecase: Optional[UpdateBillingDocumentUseCase] = None
    update_billing_document_status_usecase: Optional[UpdateBillingDocumentStatusUseCase] = None
    list_billing_documents_usecase: Optional[ListBillingDocumentsUseCase] = None
    get_billing_document_usecase: Optional[GetBillingDocumentUseCase] = None
    delete_billing_document_usecase: Optional[DeleteBillingDocumentUseCase] = None
    render_billing_document_pdf_usecase: Optional[RenderBillingDocumentPdfUseCase] = None

    # billing-template use-cases
    create_billing_template_usecase: Optional[CreateTemplateUseCase] = None
    update_billing_template_usecase: Optional[UpdateTemplateUseCase] = None
    list_billing_templates_usecase: Optional[ListTemplatesUseCase] = None
    get_billing_template_usecase: Optional[GetTemplateUseCase] = None
    delete_billing_template_usecase: Optional[DeleteTemplateUseCase] = None
    apply_template_usecase: Optional[ApplyTemplateToCreateDocumentUseCase] = None

    # company-profile use-cases
    get_company_profile_usecase: Optional[GetCompanyProfileUseCase] = None
    upsert_company_profile_usecase: Optional[UpsertCompanyProfileUseCase] = None

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
    export_labor_usecase: Optional[ExportLaborUseCase] = None


# =============================================================================
# EMAIL PORT FACTORY
# =============================================================================

# Module-level singleton for InMemoryEmailAdapter so tests can inspect .sent
_inmemory_email_adapter: Optional[Any] = None


def _build_email_port() -> Any:
    """
    Instantiate the correct email adapter based on EMAIL_PROVIDER env var.

    Supported values: 'resend', 'inmemory', 'smtp' (smtp keeps legacy path).
    Defaults to 'smtp' when unset.
    """
    global _inmemory_email_adapter

    provider = os.environ.get("EMAIL_PROVIDER", "smtp").lower()

    if provider == "resend":
        from app.infrastructure.email.resend_adapter import ResendEmailAdapter

        api_key = os.environ.get("RESEND_API_KEY", "")
        from_email = os.environ.get("FROM_EMAIL", "")
        return ResendEmailAdapter(api_key=api_key, from_email=from_email)

    if provider == "inmemory":
        from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter

        if _inmemory_email_adapter is None:
            _inmemory_email_adapter = InMemoryEmailAdapter()
        return _inmemory_email_adapter

    # 'smtp' or any unknown value — no email adapter wired. The invitation
    # use-cases will fail loudly if they try to enqueue a send while
    # email_port is None, which is the right signal in non-prod/non-test envs.
    return None


def _build_email_renderer() -> Any:
    """Return an EmailRenderer pointed at the bundled Jinja2 templates directory."""
    import pathlib
    from app.infrastructure.email.renderer import EmailRenderer

    templates_dir = str(pathlib.Path(__file__).parent / "app" / "infrastructure" / "email" / "templates")
    return EmailRenderer(templates_dir=templates_dir)


class _DirectEmailQueue:
    """Fallback queue that sends emails inline (no Redis/Celery required).

    Used when no real queue_service is configured (e.g. tests, dev with inmemory email).
    The CreateInvitationUseCase calls queue.enqueue('tasks.send_email', {'payload': p}).
    """

    def __init__(self, email_port: Any) -> None:
        self._email_port = email_port

    def enqueue(self, task_name: str, payload: Dict[str, Any]) -> str:
        """Execute the email send inline instead of queuing."""
        if self._email_port is None:
            return "noop"
        if task_name == "tasks.send_email":
            ep = payload.get("payload")
            if ep is not None:
                try:
                    self._email_port.send(ep)
                except Exception:
                    import logging

                    logging.getLogger(__name__).warning("Inline email send failed for task %s", task_name)
        return "inline"


# Global container instance
container = Container()


def configure_container(
    queue_service: Optional[QueuePort] = None,
    project_repository: Optional[ProjectRepository] = None,
    user_repository: Optional[UserRepositoryPort] = None,
    password_hasher: Optional[PasswordHasherPort] = None,
    token_issuer: Optional[TokenIssuerPort] = None,
    session_manager: Optional[SessionManagerPort] = None,
    worker_repository: Optional[IWorkerRepository] = None,
    labor_entry_repository: Optional[ILaborEntryRepository] = None,
    invoice_repository: Optional[IInvoiceRepository] = None,
    attachment_storage: Optional[IAttachmentStorage] = None,
    invoice_attachment_repository: Optional[IInvoiceAttachmentRepository] = None,
    task_repository: Optional[ITaskRepository] = None,
    invitation_repo: Optional[Any] = None,
    project_membership_repo: Optional[Any] = None,
    role_repo: Optional[Any] = None,
) -> Container:
    """
    Configure the dependency injection container.

    This should be called once at application startup to wire up
    infrastructure implementations to their ports.
    """
    global container

    container = Container(
        queue_service=queue_service,
        project_repository=project_repository,
        user_repository=user_repository,
        password_hasher=password_hasher,
        token_issuer=token_issuer,
        session_manager=session_manager,
        worker_repository=worker_repository,
        labor_entry_repository=labor_entry_repository,
        invoice_repository=invoice_repository,
        attachment_storage=attachment_storage,
        invoice_attachment_repository=invoice_attachment_repository,
        task_repository=task_repository,
        invitation_repo=invitation_repo,
        project_membership_repo=project_membership_repo,
        role_repository=role_repo,
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

    if worker_repository and labor_entry_repository and project_repository:
        container.export_labor_usecase = ExportLaborUseCase(
            worker_repo=worker_repository,
            entry_repo=labor_entry_repository,
            summary_usecase=GetLaborSummaryUseCase(labor_entry_repository),
            list_entries_usecase=ListLaborEntriesUseCase(worker_repository, labor_entry_repository),
            project_repo=project_repository,
        )

    # Wire invoice use cases
    if invoice_repository:
        container.create_invoice_usecase = CreateInvoiceUseCase(invoice_repository)
        container.list_invoices_usecase = ListInvoicesUseCase(invoice_repository)
        container.get_invoice_usecase = GetInvoiceUseCase(invoice_repository)
        container.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repository)
        # DeleteInvoice gets the attachment repo + storage so it can clean S3 before
        # the FK CASCADE drops the metadata rows (no S3 orphans on invoice delete).
        container.delete_invoice_usecase = DeleteInvoiceUseCase(
            invoice_repository,
            invoice_attachment_repository,
            attachment_storage,
        )

    # Wire invoice export use case (requires both invoice_repository and project_repository)
    if invoice_repository and project_repository:
        container.export_invoices_usecase = ExportInvoicesUseCase(
            invoice_repo=invoice_repository,
            project_repo=project_repository,
        )

    # Wire task (planning) use cases
    if task_repository:
        container.create_task_usecase = CreateTaskUseCase(task_repository)
        container.list_tasks_usecase = ListTasksUseCase(task_repository)
        container.get_task_usecase = GetTaskUseCase(task_repository)
        container.update_task_usecase = UpdateTaskUseCase(task_repository)
        container.move_task_usecase = MoveTaskUseCase(task_repository)
        container.delete_task_usecase = DeleteTaskUseCase(task_repository)

    # Wire attachment use cases
    if invoice_repository and invoice_attachment_repository and attachment_storage:
        container.upload_attachment_usecase = UploadAttachmentUseCase(
            invoice_repository, invoice_attachment_repository, attachment_storage
        )
        container.list_attachments_usecase = ListAttachmentsUseCase(invoice_attachment_repository)
        container.get_attachment_usecase = GetAttachmentUseCase(invoice_attachment_repository, attachment_storage)
        container.delete_attachment_usecase = DeleteAttachmentUseCase(invoice_attachment_repository, attachment_storage)

    # Wire email port + renderer
    container.email_port = _build_email_port()
    container.email_renderer = _build_email_renderer()

    # Wire invitation use cases (phase 05)
    app_base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")
    if (
        invitation_repo is not None
        and project_membership_repo is not None
        and project_repository is not None
        and role_repo is not None
        and user_repository is not None
    ):
        # InMemory queue shim — use email_port directly if no real queue configured
        _queue = queue_service if queue_service is not None else _DirectEmailQueue(container.email_port)

        # H2 — CreateInvitationUseCase + BulkAddExistingUserUseCase now own their
        # transaction boundary: they commit explicitly before enqueueing emails so the
        # queue write only fires for state that persisted. Inject the SQLAlchemy session.
        from app import db as _db

        container.create_invitation_usecase = CreateInvitationUseCase(
            invitation_repo=invitation_repo,
            project_membership_repo=project_membership_repo,
            user_repo=user_repository,
            project_repo=project_repository,
            role_repo=role_repo,
            email_port=container.email_port,
            email_renderer=container.email_renderer,
            queue_port=_queue,
            app_base_url=app_base_url,
            db_session=_db.session,
        )
        container.verify_invitation_usecase = VerifyInvitationUseCase(
            invitation_repo=invitation_repo,
            project_repo=project_repository,
            role_repo=role_repo,
            user_repo=user_repository,
        )
        container.revoke_invitation_usecase = RevokeInvitationUseCase(
            invitation_repo=invitation_repo,
            user_repo=user_repository,
        )
        container.list_invitations_usecase = ListInvitationsUseCase(
            invitation_repo=invitation_repo,
            project_membership_repo=project_membership_repo,
            role_repo=role_repo,
            user_repo=user_repository,
        )

        # AcceptInvitationUseCase needs a db session; lazily import db here
        if password_hasher is not None and token_issuer is not None:
            from app import db as _db

            container.accept_invitation_usecase = AcceptInvitationUseCase(
                invitation_repo=invitation_repo,
                user_repo=user_repository,
                project_membership_repo=project_membership_repo,
                password_hasher=password_hasher,
                token_issuer=token_issuer,
                db_session=_db.session,
            )

    # Wire admin use cases (requires user, project, role, membership repos)
    if (
        user_repository is not None
        and project_repository is not None
        and role_repo is not None
        and project_membership_repo is not None
    ):
        _queue = queue_service if queue_service is not None else _DirectEmailQueue(container.email_port)
        from app import db as _db

        container.bulk_add_existing_user_usecase = BulkAddExistingUserUseCase(
            user_repo=user_repository,
            project_repo=project_repository,
            role_repo=role_repo,
            membership_repo=project_membership_repo,
            email_renderer=container.email_renderer,
            queue_port=_queue,
            app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:3000"),
            db_session=_db.session,
        )

    # Wire notes use cases (phase 03) — always wired; repos are instantiated in
    # _configure_di_container() which injects db.session at app startup.
    # Note: note_repo, note_dismissal_repo, and note_membership_reader are set
    # directly on the container after this function returns (see app/__init__.py).

    return container


def get_container() -> Container:
    """Get the current container instance."""
    return container
