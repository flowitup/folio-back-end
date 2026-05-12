"""
Construction Backend Application Factory

This module provides the create_app() function which sets up and configures
the Flask application following the application factory pattern.
"""

import os

from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from config import Config
from app.infrastructure.database.models import Base
from app.infrastructure.rate_limiter import limiter

# Create SQLAlchemy with our custom Base's metadata
db = SQLAlchemy(model_class=Base)
migrate = Migrate()
jwt = JWTManager()


def create_app(config_class: type = Config) -> Flask:
    """
    Application factory function.

    Args:
        config_class: Configuration class to use (default: Config)

    Returns:
        Configured Flask application instance
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Production security check — fail fast rather than run with insecure defaults
    if os.environ.get("FLASK_ENV") == "production":
        # Cheapest env-only check first: FLASK_DEV_INSECURE only relaxes cookie flags in
        # non-prod and must never be honored in production.
        if os.environ.get("FLASK_DEV_INSECURE") == "1":
            raise RuntimeError("CRITICAL: FLASK_DEV_INSECURE=1 is not permitted when FLASK_ENV=production.")
        jwt_secret = getattr(config_class, "JWT_SECRET_KEY", "")
        if not jwt_secret or "dev-" in jwt_secret.lower():
            raise RuntimeError("CRITICAL: JWT_SECRET_KEY must be set in production.")
        secret_key = getattr(config_class, "SECRET_KEY", "")
        if not secret_key or "dev-" in secret_key.lower():
            raise RuntimeError("CRITICAL: SECRET_KEY must be set in production.")

    # Catch the dev-trap: a non-production environment behind a public https origin
    # would issue cookies without Secure / CSRF (in legacy FLASK_DEV_INSECURE mode).
    if (
        os.environ.get("FLASK_ENV") != "production"
        and os.environ.get("FLASK_DEV_INSECURE") == "1"
        and "https://" in os.environ.get("CORS_ORIGINS", "")
    ):
        raise RuntimeError(
            "CRITICAL: FLASK_DEV_INSECURE=1 is incompatible with https:// CORS origins. "
            "Either drop FLASK_DEV_INSECURE or use http://localhost-style origins for dev."
        )

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Reject upload bodies > 10 MB at the WSGI layer (matches attachment use-case cap)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    # Initialize extensions
    cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")]
    # expose_headers is critical for binary download endpoints (e.g. labor-export):
    # cross-origin browser fetches cannot read non-default-safelisted headers without
    # explicit exposure. Without this, fetchLaborExport's filename parsing falls back
    # to a generic timestamped name in production.
    CORS(
        app,
        supports_credentials=True,
        origins=cors_origins,
        expose_headers=["Content-Disposition", "X-Content-Type-Options"],
    )
    db.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)

    # Configure JWT handlers
    from app.infrastructure.jwt_handlers import configure_jwt_handlers

    configure_jwt_handlers(jwt)

    # Configure dependency injection container
    with app.app_context():
        _configure_di_container()

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health_check():
        """Health check endpoint for monitoring."""
        return jsonify({"status": "ok"})

    # Register blueprints
    from app.api.v1 import bp as api_v1_bp
    from app.api.v1.auth import auth_bp
    from app.api.v1.projects import projects_bp
    from app.api.v1.labor import labor_bp
    from app.api.v1.labor.export_routes import labor_export_bp
    from app.api.v1.invoices import invoice_bp
    from app.api.v1.invoices.export_routes import invoice_export_bp
    from app.api.v1.tasks import task_bp
    from app.api.v1.invitations import invitations_bp
    from app.api.v1.roles import roles_bp
    from app.api.v1.admin import admin_bp
    from app.api.v1.notes import notes_bp
    from app.api.v1.notifications import notifications_bp
    from app.api.v1.billing import billing_documents_bp, billing_templates_bp
    from app.api.v1.companies import companies_bp, users_me_bp
    from app.api.v1.persons import persons_bp

    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/v1/projects")
    app.register_blueprint(labor_bp, url_prefix="/api/v1")
    app.register_blueprint(labor_export_bp, url_prefix="/api/v1")
    app.register_blueprint(invoice_bp, url_prefix="/api/v1")
    app.register_blueprint(invoice_export_bp, url_prefix="/api/v1")
    app.register_blueprint(task_bp, url_prefix="/api/v1")
    app.register_blueprint(invitations_bp, url_prefix="/api/v1/invitations")
    app.register_blueprint(roles_bp, url_prefix="/api/v1/roles")
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(notes_bp, url_prefix="/api/v1")
    app.register_blueprint(notifications_bp, url_prefix="/api/v1")
    app.register_blueprint(billing_documents_bp, url_prefix="/api/v1")
    app.register_blueprint(billing_templates_bp, url_prefix="/api/v1")
    # company_profile_bp retired — table dropped in migration 2d9c35848b9b (C2)
    app.register_blueprint(companies_bp, url_prefix="/api/v1")
    app.register_blueprint(users_me_bp, url_prefix="/api/v1")
    app.register_blueprint(persons_bp, url_prefix="/api/v1")

    # Test-only blueprint: exposes InMemoryEmailAdapter state for e2e tests.
    # MUST only be registered when TESTING=True — never in production.
    if app.config.get("TESTING"):
        from app.api.v1.test_only.routes import test_only_bp

        app.register_blueprint(test_only_bp, url_prefix="/api/v1/__test__")

    # Initialize Swagger API documentation
    from app.api.swagger import init_swagger

    init_swagger(app)

    return app


def _configure_di_container() -> None:
    """Configure the dependency injection container."""
    from wiring import configure_container
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
    from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_invoice_attachment import SQLAlchemyInvoiceAttachmentRepository
    from app.infrastructure.adapters.sqlalchemy_task import SQLAlchemyTaskRepository
    from app.infrastructure.adapters.s3_attachment_storage import S3AttachmentStorage
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.database.repositories.sqlalchemy_invitation import SqlAlchemyInvitationRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.repositories.sqlalchemy_note_repository import SqlAlchemyNoteRepository
    from app.infrastructure.database.repositories.sqlalchemy_note_dismissal_repository import (
        SqlAlchemyNoteDismissalRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership_reader import (
        SqlAlchemyProjectMembershipReader,
    )
    from app.application.notes.create_note_usecase import CreateNoteUseCase
    from app.application.notes.list_project_notes_usecase import ListProjectNotesUseCase
    from app.application.notes.update_note_usecase import UpdateNoteUseCase
    from app.application.notes.delete_note_usecase import DeleteNoteUseCase
    from app.application.notes.mark_note_done_usecase import MarkNoteDoneUseCase
    from app.application.notes.mark_note_open_usecase import MarkNoteOpenUseCase
    from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
    from app.application.notes.dismiss_notification_usecase import DismissNotificationUseCase
    from config import Config

    storage = S3AttachmentStorage(
        endpoint_url=Config.S3_ENDPOINT_URL,
        access_key=Config.S3_ACCESS_KEY,
        secret_key=Config.S3_SECRET_KEY,
        bucket=Config.S3_BUCKET,
        region=Config.S3_REGION,
    )
    # Best-effort bucket bootstrap — log but do not crash the app on transient S3 outage
    try:
        storage.ensure_bucket()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("S3 bucket bootstrap failed: %s (uploads will fail until resolved)", exc)

    invitation_repo = SqlAlchemyInvitationRepository(db.session)
    membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
    role_repo = SqlAlchemyRoleRepository(db.session)

    configure_container(
        user_repository=SQLAlchemyUserRepository(db.session),
        project_repository=SQLAlchemyProjectRepository(db.session),
        worker_repository=SQLAlchemyWorkerRepository(db.session),
        labor_entry_repository=SQLAlchemyLaborEntryRepository(db.session),
        invoice_repository=SQLAlchemyInvoiceRepository(db.session),
        attachment_storage=storage,
        invoice_attachment_repository=SQLAlchemyInvoiceAttachmentRepository(db.session),
        task_repository=SQLAlchemyTaskRepository(db.session),
        password_hasher=Argon2PasswordHasher(),
        token_issuer=JWTTokenIssuer(redis_url=Config.REDIS_URL),
        session_manager=FlaskSessionManager(),
        invitation_repo=invitation_repo,
        project_membership_repo=membership_repo,
        role_repo=role_repo,
    )

    # Wire notes use-cases — done post-configure_container so we can pass db.session
    # directly without adding more params to configure_container's signature.
    from wiring import get_container as _get_container

    _c = _get_container()
    _note_repo = SqlAlchemyNoteRepository(db.session)
    _dismissal_repo = SqlAlchemyNoteDismissalRepository(db.session)
    _membership_reader = SqlAlchemyProjectMembershipReader(db.session)

    _c.note_repo = _note_repo
    _c.note_dismissal_repo = _dismissal_repo
    _c.note_membership_reader = _membership_reader

    _c.create_note_usecase = CreateNoteUseCase(
        note_repo=_note_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.list_project_notes_usecase = ListProjectNotesUseCase(
        note_repo=_note_repo,
        membership_reader=_membership_reader,
    )
    _c.update_note_usecase = UpdateNoteUseCase(
        note_repo=_note_repo,
        dismissal_repo=_dismissal_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.delete_note_usecase = DeleteNoteUseCase(
        note_repo=_note_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.mark_note_done_usecase = MarkNoteDoneUseCase(
        note_repo=_note_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.mark_note_open_usecase = MarkNoteOpenUseCase(
        note_repo=_note_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.list_due_notifications_usecase = ListDueNotificationsUseCase(
        note_query=_note_repo,  # SqlAlchemyNoteRepository also implements NoteQueryPort
    )
    _c.dismiss_notification_usecase = DismissNotificationUseCase(
        note_repo=_note_repo,
        dismissal_repo=_dismissal_repo,
        membership_reader=_membership_reader,
        db_session=db.session,
    )

    # -----------------------------------------------------------------------
    # Companies DI wiring (phase 03)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_company_invite_token_repository import (
        SqlAlchemyCompanyInviteTokenRepository,
    )
    from app.infrastructure.security.argon2_hasher import Argon2Hasher
    from app.infrastructure.security.secure_token_generator import SecureTokenGenerator
    from app.application.companies import (
        CreateCompanyUseCase as _CreateCompanyUseCase,
        UpdateCompanyUseCase as _UpdateCompanyUseCase,
        DeleteCompanyUseCase as _DeleteCompanyUseCase,
        ListAllCompaniesUseCase as _ListAllCompaniesUseCase,
        GenerateInviteTokenUseCase as _GenerateInviteTokenUseCase,
        RevokeInviteTokenUseCase as _RevokeInviteTokenUseCase,
        ListAttachedUsersUseCase as _ListAttachedUsersUseCase,
        BootAttachedUserUseCase as _BootAttachedUserUseCase,
        ListMyCompaniesUseCase as _ListMyCompaniesUseCase,
        GetCompanyUseCase as _GetCompanyUseCase,
        RedeemInviteTokenUseCase as _RedeemInviteTokenUseCase,
        SetPrimaryCompanyUseCase as _SetPrimaryCompanyUseCase,
        DetachCompanyUseCase as _DetachCompanyUseCase,
    )
    import datetime as _dt

    class _UtcClock:
        """Minimal ClockPort implementation returning UTC now."""

        def now(self) -> _dt.datetime:
            return _dt.datetime.now(_dt.timezone.utc)

    _company_repo = SqlAlchemyCompanyRepository(db.session)
    _access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
    _token_repo = SqlAlchemyCompanyInviteTokenRepository(db.session)
    _argon2_hasher = Argon2Hasher()
    _token_generator = SecureTokenGenerator()
    _clock = _UtcClock()
    # Reuse authorization_service as RoleCheckerPort (structurally compatible)
    _role_checker = _c.authorization_service

    _c.company_repo = _company_repo
    _c.user_company_access_repo = _access_repo
    _c.company_invite_token_repo = _token_repo

    # admin use-cases
    _c.create_company_usecase = _CreateCompanyUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.update_company_usecase = _UpdateCompanyUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.delete_company_usecase = _DeleteCompanyUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.list_all_companies_usecase = _ListAllCompaniesUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.generate_invite_token_usecase = _GenerateInviteTokenUseCase(
        company_repo=_company_repo,
        token_repo=_token_repo,
        hasher=_argon2_hasher,
        token_generator=_token_generator,
        clock=_clock,
        role_checker=_role_checker,
    )
    _c.revoke_invite_token_usecase = _RevokeInviteTokenUseCase(
        company_repo=_company_repo,
        token_repo=_token_repo,
        role_checker=_role_checker,
    )
    _c.list_attached_users_usecase = _ListAttachedUsersUseCase(
        company_repo=_company_repo,
        access_repo=_access_repo,
        role_checker=_role_checker,
    )
    _c.boot_attached_user_usecase = _BootAttachedUserUseCase(
        company_repo=_company_repo,
        access_repo=_access_repo,
        role_checker=_role_checker,
    )
    # user use-cases
    _c.list_my_companies_usecase = _ListMyCompaniesUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.get_company_usecase = _GetCompanyUseCase(
        company_repo=_company_repo,
        access_repo=_access_repo,
        role_checker=_role_checker,
    )
    _c.redeem_invite_token_usecase = _RedeemInviteTokenUseCase(
        token_repo=_token_repo,
        access_repo=_access_repo,
        hasher=_argon2_hasher,
        clock=_clock,
    )
    _c.set_primary_company_usecase = _SetPrimaryCompanyUseCase(
        access_repo=_access_repo,
    )
    _c.detach_company_usecase = _DetachCompanyUseCase(
        access_repo=_access_repo,
    )

    # -----------------------------------------------------------------------
    # Persons DI wiring (Phase 1b-ii of labor-calendar-and-bulk-log plan)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_person_repository import (
        SqlAlchemyPersonRepository,
    )
    from app.application.persons import (
        CreatePersonUseCase as _CreatePersonUseCase,
        SearchPersonsUseCase as _SearchPersonsUseCase,
        MergePersonsUseCase as _MergePersonsUseCase,
    )

    _person_repo = SqlAlchemyPersonRepository(db.session)
    _c.person_repo = _person_repo
    _c.create_person_usecase = _CreatePersonUseCase(person_repo=_person_repo)
    _c.search_persons_usecase = _SearchPersonsUseCase(person_repo=_person_repo)
    _c.merge_persons_usecase = _MergePersonsUseCase(
        person_repo=_person_repo,
        db_session=db.session,
    )

    # -----------------------------------------------------------------------
    # Billing DI wiring (phase 04)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_billing_document_repository import (
        SqlAlchemyBillingDocumentRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_billing_template_repository import (
        SqlAlchemyBillingTemplateRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
        SqlAlchemyBillingNumberCounterRepository,
    )
    from app.infrastructure.pdf.billing_document_pdf_renderer import (
        ReportLabBillingDocumentPdfRenderer,
    )
    from app.infrastructure.xlsx.billing_document_xlsx_renderer import (
        OpenpyxlBillingDocumentXlsxRenderer,
    )
    from app.application.billing import (
        CreateBillingDocumentUseCase,
        ImportBillingDocumentUseCase,
        ListActivitySuggestionsUseCase,
        CloneBillingDocumentUseCase,
        ConvertDevisToFactureUseCase,
        UpdateBillingDocumentUseCase,
        UpdateBillingDocumentStatusUseCase,
        ListBillingDocumentsUseCase,
        GetBillingDocumentUseCase,
        DeleteBillingDocumentUseCase,
        RenderBillingDocumentPdfUseCase,
        RenderBillingDocumentXlsxUseCase,
        CreateTemplateUseCase,
        UpdateTemplateUseCase,
        ListTemplatesUseCase,
        GetTemplateUseCase,
        DeleteTemplateUseCase,
        ApplyTemplateToCreateDocumentUseCase,
    )

    _billing_doc_repo = SqlAlchemyBillingDocumentRepository(db.session)
    _billing_tpl_repo = SqlAlchemyBillingTemplateRepository(db.session)
    _billing_counter_repo = SqlAlchemyBillingNumberCounterRepository(db.session)
    _billing_pdf_renderer = ReportLabBillingDocumentPdfRenderer()
    _billing_xlsx_renderer = OpenpyxlBillingDocumentXlsxRenderer()
    # project_repository is already wired via configure_container
    _project_repo = _c.project_repository

    _c.billing_document_repo = _billing_doc_repo
    _c.billing_template_repo = _billing_tpl_repo
    _c.billing_counter_repo = _billing_counter_repo
    _c.billing_pdf_renderer = _billing_pdf_renderer
    _c.billing_xlsx_renderer = _billing_xlsx_renderer

    # billing-document use-cases
    _c.create_billing_document_usecase = CreateBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
        counter_repo=_billing_counter_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
        company_repo=_company_repo,  # company_id path
        access_repo=_access_repo,  # attachment validation
    )
    _c.import_billing_document_usecase = ImportBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
        counter_repo=_billing_counter_repo,
        company_repo=_company_repo,
        access_repo=_access_repo,
    )
    _c.list_activity_suggestions_usecase = ListActivitySuggestionsUseCase(
        doc_repo=_billing_doc_repo,
    )
    _c.clone_billing_document_usecase = CloneBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
        counter_repo=_billing_counter_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
        company_repo=_company_repo,
        access_repo=_access_repo,
    )
    _c.convert_devis_to_facture_usecase = ConvertDevisToFactureUseCase(
        doc_repo=_billing_doc_repo,
        counter_repo=_billing_counter_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
        company_repo=_company_repo,
        access_repo=_access_repo,
    )
    _c.update_billing_document_usecase = UpdateBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
    )
    _c.update_billing_document_status_usecase = UpdateBillingDocumentStatusUseCase(
        doc_repo=_billing_doc_repo,
    )
    _c.list_billing_documents_usecase = ListBillingDocumentsUseCase(
        doc_repo=_billing_doc_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
    )
    _c.get_billing_document_usecase = GetBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
    )
    _c.delete_billing_document_usecase = DeleteBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
    )
    _c.render_billing_document_pdf_usecase = RenderBillingDocumentPdfUseCase(
        doc_repo=_billing_doc_repo,
        pdf_renderer=_billing_pdf_renderer,
    )
    _c.render_billing_document_xlsx_usecase = RenderBillingDocumentXlsxUseCase(
        doc_repo=_billing_doc_repo,
        xlsx_renderer=_billing_xlsx_renderer,
    )

    # billing-template use-cases
    _c.create_billing_template_usecase = CreateTemplateUseCase(
        template_repo=_billing_tpl_repo,
    )
    _c.update_billing_template_usecase = UpdateTemplateUseCase(
        template_repo=_billing_tpl_repo,
    )
    _c.list_billing_templates_usecase = ListTemplatesUseCase(
        template_repo=_billing_tpl_repo,
    )
    _c.get_billing_template_usecase = GetTemplateUseCase(
        template_repo=_billing_tpl_repo,
    )
    _c.delete_billing_template_usecase = DeleteTemplateUseCase(
        template_repo=_billing_tpl_repo,
    )
    _c.apply_template_usecase = ApplyTemplateToCreateDocumentUseCase(
        doc_repo=_billing_doc_repo,
        template_repo=_billing_tpl_repo,
        counter_repo=_billing_counter_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
        company_repo=_company_repo,
        access_repo=_access_repo,
    )
