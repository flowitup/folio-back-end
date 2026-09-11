"""
Construction Backend Application Factory

This module provides the create_app() function which sets up and configures
the Flask application following the application factory pattern.
"""

import os
from typing import Any

from flask import Flask, current_app, jsonify
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
    _flask_env = os.environ.get("FLASK_ENV", "development")
    _is_production = _flask_env == "production"
    _is_development = _flask_env == "development"
    if _is_production:
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
        # Default MinIO credentials and a localhost S3 endpoint indicate the
        # operator forgot to point at the real object store; refusing to boot
        # is safer than silently writing tenant attachments to a local bucket.
        s3_access = getattr(config_class, "S3_ACCESS_KEY", "")
        s3_secret = getattr(config_class, "S3_SECRET_KEY", "")
        s3_endpoint = getattr(config_class, "S3_ENDPOINT_URL", "") or ""
        if s3_access == "minioadmin" or s3_secret == "minioadmin":
            raise RuntimeError(
                "CRITICAL: S3_ACCESS_KEY / S3_SECRET_KEY must not use the default minioadmin in production."
            )
        if "localhost" in s3_endpoint or "127.0.0.1" in s3_endpoint:
            raise RuntimeError("CRITICAL: S3_ENDPOINT_URL must not point at localhost in production.")

    # FLASK_DEV_INSECURE relaxes cookie flags (Secure=False, SameSite=None, CSRF=False)
    # and must only ever be honored in a true local-development context. Allowing it
    # in staging/UAT would let any cross-origin request mount a CSRF.
    if os.environ.get("FLASK_DEV_INSECURE") == "1" and not _is_development:
        raise RuntimeError(
            "CRITICAL: FLASK_DEV_INSECURE=1 is only permitted when FLASK_ENV=development. "
            "Use proper TLS + Secure cookies for staging/UAT."
        )

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Flask cap above the 150 MiB use-case limit (PROJECT_DOCUMENT_MAX_SIZE_BYTES)
    # so multipart envelope overhead doesn't false-positive a 413 before the use-case
    # can return its richer error. Invoice attachments have their own 10 MB cap enforced
    # in the use-case layer (upload_attachment.py), so this bump does not relax that limit.
    # Multipart POST through CF is capped at 100 MB; presigned uploads bypass CF.
    app.config["MAX_CONTENT_LENGTH"] = 151 * 1024 * 1024

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

    # Clear the per-request authz resolver caches (app.api.v1.authz_context)
    # at the start of every request. Flask reuses an already-pushed
    # AppContext for a request whose app matches the top of the stack, so a
    # test fixture (or any code) holding one `app.app_context()` open across
    # several `test_client()` calls would otherwise share the SAME `flask.g`
    # — and therefore a stale authz memo — across those calls. A role or D8
    # grant/deny change must take effect on the very next request.
    @app.before_request
    def _clear_authz_request_memo() -> None:
        from app.api.v1.authz_context import clear_request_memo

        clear_request_memo()

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
    from app.api.v1.admin import admin_bp
    from app.api.v1.chiffrage import chiffrage_bp
    from app.api.v1.notes import notes_bp
    from app.api.v1.chat import chat_bp
    from app.api.v1.notifications import notifications_bp
    from app.api.v1.push import push_bp
    from app.api.v1.billing import billing_documents_bp, billing_templates_bp
    from app.api.v1.companies import companies_bp, users_me_bp
    from app.api.v1.persons import persons_bp
    from app.api.v1.payment_methods import payment_methods_bp
    from app.api.v1.project_documents import project_documents_bp
    from app.api.v1.project_analyses import project_analyses_bp
    from app.api.v1.project_photos import project_photos_bp
    from app.api.v1.bibliotheque import bibliotheque_bp

    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/v1/projects")
    app.register_blueprint(labor_bp, url_prefix="/api/v1")
    app.register_blueprint(labor_export_bp, url_prefix="/api/v1")
    app.register_blueprint(invoice_bp, url_prefix="/api/v1")
    app.register_blueprint(invoice_export_bp, url_prefix="/api/v1")
    app.register_blueprint(task_bp, url_prefix="/api/v1")
    app.register_blueprint(chiffrage_bp, url_prefix="/api/v1")
    app.register_blueprint(invitations_bp, url_prefix="/api/v1/invitations")
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(notes_bp, url_prefix="/api/v1")
    app.register_blueprint(chat_bp, url_prefix="/api/v1")
    app.register_blueprint(notifications_bp, url_prefix="/api/v1")
    app.register_blueprint(push_bp, url_prefix="/api/v1")
    app.register_blueprint(billing_documents_bp, url_prefix="/api/v1")
    app.register_blueprint(billing_templates_bp, url_prefix="/api/v1")
    # company_profile_bp retired — table dropped in migration 2d9c35848b9b (C2)
    app.register_blueprint(companies_bp, url_prefix="/api/v1")
    app.register_blueprint(users_me_bp, url_prefix="/api/v1")
    app.register_blueprint(persons_bp, url_prefix="/api/v1")
    app.register_blueprint(payment_methods_bp, url_prefix="/api/v1")
    app.register_blueprint(project_documents_bp, url_prefix="/api/v1")
    app.register_blueprint(project_analyses_bp, url_prefix="/api/v1")
    app.register_blueprint(project_photos_bp, url_prefix="/api/v1")
    app.register_blueprint(bibliotheque_bp, url_prefix="/api/v1")

    # Test-only blueprint: exposes InMemoryEmailAdapter state for e2e tests.
    # MUST only be registered when TESTING=True — never in production.
    if app.config.get("TESTING"):
        from app.api.v1.test_only.routes import test_only_bp

        app.register_blueprint(test_only_bp, url_prefix="/api/v1/__test__")

    # Initialize OpenAPI documentation. The doc surface is recon-rich
    # (full schema map + endpoint catalogue) so it is disabled in
    # production unless EXPOSE_DOCS=1 forces it on.
    _is_production = os.environ.get("FLASK_ENV") == "production"
    if (not _is_production) or app.config.get("EXPOSE_DOCS"):
        from app.api.openapi import init_openapi

        init_openapi(app)

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
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.database.repositories.sqlalchemy_invitation import SqlAlchemyInvitationRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
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
    from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
    from app.application.notes.dismiss_notification_usecase import DismissNotificationUseCase
    from config import Config

    storage = S3AttachmentStorage(
        endpoint_url=Config.S3_ENDPOINT_URL,
        access_key=Config.S3_ACCESS_KEY,
        secret_key=Config.S3_SECRET_KEY,
        bucket=Config.S3_BUCKET,
        region=Config.S3_REGION,
        public_endpoint_url=Config.S3_PUBLIC_ENDPOINT_URL,
    )
    # Best-effort bucket bootstrap — log but do not crash the app on transient S3 outage
    try:
        storage.ensure_bucket()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("S3 bucket bootstrap failed: %s (uploads will fail until resolved)", exc)

    # Configure CORS for presigned-URL direct uploads
    if Config.S3_PUBLIC_ENDPOINT_URL:
        import logging as _cors_log

        _cors_origin = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")[0].strip()
        try:
            storage.ensure_cors([_cors_origin])
        except Exception as exc:
            _cors_log.getLogger(__name__).warning("S3 CORS setup failed: %s", exc)

    invitation_repo = SqlAlchemyInvitationRepository(db.session)
    membership_repo = SqlAlchemyProjectMembershipRepository(db.session)

    configure_container(
        user_repository=SQLAlchemyUserRepository(db.session),
        project_repository=SQLAlchemyProjectRepository(db.session),
        worker_repository=SQLAlchemyWorkerRepository(db.session),
        labor_entry_repository=SQLAlchemyLaborEntryRepository(db.session),
        invoice_repository=SQLAlchemyInvoiceRepository(db.session),
        attachment_storage=storage,
        invoice_attachment_repository=SQLAlchemyInvoiceAttachmentRepository(db.session),
        task_repository=SQLAlchemyTaskRepository(db.session),
        token_issuer=JWTTokenIssuer(redis_url=Config.REDIS_URL),
        session_manager=FlaskSessionManager(),
        invitation_repo=invitation_repo,
        project_membership_repo=membership_repo,
    )

    # Wire labor activity use-cases
    from app.infrastructure.adapters.sqlalchemy_labor_activity import SQLAlchemyLaborActivityRepository
    from app.application.labor.labor_activity_usecases import (
        CreateLaborActivityUseCase,
        ListLaborActivitiesUseCase,
        UpdateLaborActivityUseCase,
        DeleteLaborActivityUseCase,
    )
    from wiring import get_container as _get_container

    _c = _get_container()

    # -----------------------------------------------------------------------
    # Project spent reader (labor + non-released_funds invoices)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader as _ProjectSpentReader,
    )

    _c.project_spent_reader = _ProjectSpentReader(db.session)

    _activity_repo = SQLAlchemyLaborActivityRepository(db.session)
    _c.labor_activity_repository = _activity_repo
    _c.create_labor_activity_usecase = CreateLaborActivityUseCase(_activity_repo)
    _c.list_labor_activities_usecase = ListLaborActivitiesUseCase(_activity_repo)
    _c.update_labor_activity_usecase = UpdateLaborActivityUseCase(_activity_repo)
    _c.delete_labor_activity_usecase = DeleteLaborActivityUseCase(_activity_repo)

    # Re-wire export_labor_usecase to inject the activity use-case now that the
    # activity repository is available. The use-case was constructed in configure_container
    # with list_activities_usecase=None (default); we patch it here so the PDF activity
    # log section is populated in production without changing the configure_container
    # signature (activity repo is constructed after configure_container returns).
    if _c.export_labor_usecase is not None:
        _c.export_labor_usecase._list_activities_usecase = ListLaborActivitiesUseCase(_activity_repo)

    # Wire labor day description use-cases (same late-injection pattern as activities above).
    from app.infrastructure.adapters.sqlalchemy_labor_day_description import (
        SQLAlchemyLaborDayDescriptionRepository,
    )
    from app.application.labor.labor_day_description_usecases import (
        SetLaborDayDescriptionUseCase,
        ListLaborDayDescriptionsUseCase,
    )

    _day_desc_repo = SQLAlchemyLaborDayDescriptionRepository(db.session)
    _c.labor_day_description_repository = _day_desc_repo
    _c.set_labor_day_description_usecase = SetLaborDayDescriptionUseCase(_day_desc_repo)
    _c.list_labor_day_descriptions_usecase = ListLaborDayDescriptionsUseCase(_day_desc_repo)

    # Inject day-descriptions use-case into export_labor_usecase so the PDF
    # combined Day log section is populated (mirrors _list_activities_usecase injection above).
    if _c.export_labor_usecase is not None:
        _c.export_labor_usecase._list_day_descriptions_usecase = ListLaborDayDescriptionsUseCase(_day_desc_repo)

    # Wire notes use-cases — done post-configure_container so we can pass db.session
    # directly without adding more params to configure_container's signature.
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
        membership_reader=_membership_reader,
        db_session=db.session,
    )
    _c.delete_note_usecase = DeleteNoteUseCase(
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

    # Attendance validation: worker self-log → pending → manager validate/reject;
    # the bell reads pending entries through the dedicated query adapter.
    from app.infrastructure.adapters.sqlalchemy_pending_attendance_query import (
        SQLAlchemyPendingAttendanceQuery,
    )
    from app.application.labor.submit_own_attendance import SubmitOwnAttendanceUseCase
    from app.application.labor.validate_attendance import RejectAttendanceUseCase, ValidateAttendanceUseCase
    from app.application.labor.list_pending_attendance import ListPendingAttendanceUseCase

    _c.submit_own_attendance_usecase = SubmitOwnAttendanceUseCase(
        worker_repo=_c.worker_repository, entry_repo=_c.labor_entry_repository
    )
    _c.validate_attendance_usecase = ValidateAttendanceUseCase(
        entry_repo=_c.labor_entry_repository, worker_repo=_c.worker_repository
    )
    _c.reject_attendance_usecase = RejectAttendanceUseCase(
        entry_repo=_c.labor_entry_repository, worker_repo=_c.worker_repository
    )
    _c.list_pending_attendance_usecase = ListPendingAttendanceUseCase(SQLAlchemyPendingAttendanceQuery(db.session))
    from app.application.labor.edit_own_attendance import DecideAttendanceChangeUseCase, EditOwnAttendanceUseCase

    _c.edit_own_attendance_usecase = EditOwnAttendanceUseCase(
        worker_repo=_c.worker_repository, entry_repo=_c.labor_entry_repository
    )
    _c.decide_attendance_change_usecase = DecideAttendanceChangeUseCase(
        entry_repo=_c.labor_entry_repository, worker_repo=_c.worker_repository
    )

    # Wire chat use-cases (team chat, FEATURE_CHAT). Attachments reuse the S3 storage
    # singleton under the "chat/<kind>/<channel>/<message>" key prefix.
    from app.infrastructure.database.repositories.sqlalchemy_chat_repository import SqlAlchemyChatRepository
    from app.application.chat.usecases import (
        GetAttachmentUseCase as _GetChatAttachmentUseCase,
        ListChannelsUseCase as _ListChatChannelsUseCase,
        ListMessagesUseCase as _ListChatMessagesUseCase,
        MarkChannelReadUseCase as _MarkChatChannelReadUseCase,
        SendMessageUseCase as _SendChatMessageUseCase,
    )

    _chat_repo = SqlAlchemyChatRepository(db.session)
    _c.chat_repo = _chat_repo
    _c.list_chat_channels_usecase = _ListChatChannelsUseCase(_chat_repo, _chat_repo, _chat_repo)
    _c.list_chat_messages_usecase = _ListChatMessagesUseCase(_chat_repo, _chat_repo, _chat_repo)
    _c.send_chat_message_usecase = _SendChatMessageUseCase(_chat_repo, _chat_repo, _chat_repo, storage, db.session)
    _c.mark_chat_channel_read_usecase = _MarkChatChannelReadUseCase(_chat_repo, _chat_repo, db.session)
    _c.get_chat_attachment_usecase = _GetChatAttachmentUseCase(_chat_repo, _chat_repo, storage)

    # Sign in with a phone number + SMS code. Provider picked by SMS_PROVIDER (log | twilio | gateway).
    from app.application.usecases.otp_login import (
        RequestOtpUseCase,
        RequestSignupOtpUseCase,
        VerifyOtpUseCase,
        VerifySignupOtpUseCase,
    )
    from app.infrastructure.adapters.logging_sms_sender import LoggingSmsSender
    from app.infrastructure.adapters.sms_gateway_sender import SmsGatewaySender
    from app.infrastructure.adapters.sqlalchemy_login_otp import SQLAlchemyLoginOtpRepository
    from app.infrastructure.adapters.twilio_sms_sender import TwilioSmsSender

    _cfg = current_app.config
    if _cfg.get("SMS_PROVIDER") == "twilio":
        _sms: Any = TwilioSmsSender(
            _cfg.get("TWILIO_ACCOUNT_SID", ""), _cfg.get("TWILIO_AUTH_TOKEN", ""), _cfg.get("TWILIO_FROM", "Folio")
        )
    elif _cfg.get("SMS_PROVIDER") == "gateway":
        _sms = SmsGatewaySender(
            _cfg.get("SMS_GATEWAY_URL", ""), _cfg.get("SMS_GATEWAY_USERNAME", ""), _cfg.get("SMS_GATEWAY_PASSWORD", "")
        )
    else:
        _sms = LoggingSmsSender()
    _otp_repo = SQLAlchemyLoginOtpRepository(db.session)
    _c.sms_sender = _sms

    # Push notifications around attendance (PUSH_PROVIDER log | expo).
    from app.application.push.attendance_push_notifier import AttendancePushNotifier
    from app.infrastructure.adapters.expo_push_sender import ExpoPushSender
    from app.infrastructure.adapters.logging_push_sender import LoggingPushSender
    from app.infrastructure.adapters.sqlalchemy_notification_preference import (
        SQLAlchemyNotificationPreferenceRepository,
    )
    from app.infrastructure.adapters.sqlalchemy_push_device import SQLAlchemyPushDeviceRepository

    from app.application.push.chat_push_notifier import ChatPushNotifier
    from app.application.push.dispatcher import PushDispatcher
    from app.application.push.billing_push_notifier import BillingPushNotifier
    from app.application.push.membership_push_notifier import MembershipPushNotifier
    from app.application.push.task_push_notifier import TaskPushNotifier
    from app.infrastructure.adapters.sqlalchemy_chat_push_marker import SQLAlchemyChatPushMarkerRepository

    _c.push_device_repository = SQLAlchemyPushDeviceRepository(db.session)
    _c.notification_preference_repository = SQLAlchemyNotificationPreferenceRepository(db.session)
    _c.push_sender = (
        ExpoPushSender(_cfg.get("EXPO_ACCESS_TOKEN", ""))
        if _cfg.get("PUSH_PROVIDER") == "expo"
        else LoggingPushSender()
    )
    if _c.worker_repository is not None and _c.project_repository is not None:
        _c.attendance_push_notifier = AttendancePushNotifier(
            devices=_c.push_device_repository,
            sender=_c.push_sender,
            worker_repo=_c.worker_repository,
            project_repo=_c.project_repository,
            locale=_cfg.get("PUSH_LOCALE", "vi"),
            run_async=not _cfg.get("TESTING", False),
            preferences=_c.notification_preference_repository,
        )
    # Chat pushes are coalesced per (user, channel); the notifier is attached to the
    # already-constructed send use case because the push stack is wired later than chat.
    _c.push_dispatcher = PushDispatcher(
        devices=_c.push_device_repository,
        sender=_c.push_sender,
        preferences=_c.notification_preference_repository,
        locale=_cfg.get("PUSH_LOCALE", "vi"),
        run_async=not _cfg.get("TESTING", False),
    )
    _c.chat_push_marker_repository = SQLAlchemyChatPushMarkerRepository(db.session)
    if _c.send_chat_message_usecase is not None:
        _c.send_chat_message_usecase.notifier = ChatPushNotifier(
            dispatcher=_c.push_dispatcher,
            directory=_chat_repo,
            markers=_c.chat_push_marker_repository,
            reads=_chat_repo,
            messages=_chat_repo,
            names=_chat_repo,
        )

    if _c.project_repository is not None:
        _c.task_push_notifier = TaskPushNotifier(dispatcher=_c.push_dispatcher, project_repo=_c.project_repository)

    _c.login_otp_repository = _otp_repo
    if _c.user_repository is not None and _c.authorization_service is not None and _c.token_issuer is not None:
        _c.request_otp_usecase = RequestOtpUseCase(
            _c.user_repository,
            _otp_repo,
            _sms,
            ttl_seconds=int(_cfg.get("OTP_TTL_SECONDS", 300)),
            resend_after_seconds=int(_cfg.get("OTP_RESEND_SECONDS", 60)),
            hourly_max=int(_cfg.get("OTP_HOURLY_MAX", 5)),
        )
        _c.verify_otp_usecase = VerifyOtpUseCase(
            _c.user_repository,
            _otp_repo,
            _c.authorization_service,
            _c.token_issuer,
            max_attempts=int(_cfg.get("OTP_MAX_ATTEMPTS", 5)),
        )
        # Phone self-registration: same code store as sign-in, no user until verified.
        _c.request_signup_otp_usecase = RequestSignupOtpUseCase(
            _c.user_repository,
            _otp_repo,
            _sms,
            ttl_seconds=int(_cfg.get("OTP_TTL_SECONDS", 300)),
            resend_after_seconds=int(_cfg.get("OTP_RESEND_SECONDS", 60)),
            hourly_max=int(_cfg.get("OTP_HOURLY_MAX", 5)),
        )
        _c.verify_signup_otp_usecase = VerifySignupOtpUseCase(
            _c.user_repository,
            _otp_repo,
            _c.authorization_service,
            _c.token_issuer,
            max_attempts=int(_cfg.get("OTP_MAX_ATTEMPTS", 5)),
        )
        # Invitation acceptance proves a phone by the same sign-up code flow (see
        # AcceptInvitationUseCase); its "request a code" endpoint reuses this exact
        # use case instance, gated by the invitation token instead of being open to
        # anyone. invitation_repo is wired earlier, in configure_container().
        if _c.invitation_repo is not None:
            from app.application.invitations.accept_invitation_usecase import RequestInviteOtpUseCase

            _c.request_invite_otp_usecase = RequestInviteOtpUseCase(_c.invitation_repo, _c.request_signup_otp_usecase)

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
        SetMemberRoleUseCase as _SetMemberRoleUseCase,
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

    # AuthorizationService is constructed in wiring.py before _access_repo
    # exists (it only takes a UserRepositoryPort there), so the per-company
    # role lookup used by is_company_admin() is injected here instead, once
    # SqlAlchemyUserCompanyAccessRepository is available.
    if _role_checker is not None and hasattr(_role_checker, "set_company_role_lookup"):

        def _company_role_for(user_id, company_id):
            access = _access_repo.find(user_id, company_id)
            return access.role if access is not None else None

        _role_checker.set_company_role_lookup(_company_role_for)

    _c.company_repo = _company_repo
    _c.user_company_access_repo = _access_repo
    _c.company_invite_token_repo = _token_repo

    # Membership pushes need both name sources, so they are wired here rather than in the
    # push block above, where the company repo does not exist yet.
    if _c.push_dispatcher is not None and _c.project_repository is not None:
        _c.membership_push_notifier = MembershipPushNotifier(
            dispatcher=_c.push_dispatcher,
            project_repo=_c.project_repository,
            company_repo=_company_repo,
        )
        _c.billing_push_notifier = BillingPushNotifier(
            dispatcher=_c.push_dispatcher,
            project_repo=_c.project_repository,
            access_repo=_access_repo,
        )

    # Company-aware authz resolver read port (app/domain/authz/resolver.py).
    # Wired here, alongside the other company repos, so every route that goes
    # through create_app() gets resolver-derived permissions for free.
    # cache_provider wires in the per-request memo dict (app.api.v1.authz_context)
    # so repeated sub-queries within one request (e.g. company_role_for once per
    # project while listing N projects of one company) hit cache, not the DB.
    from app.api.v1.authz_context import get_reader_cache
    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import (
        SqlAlchemyAuthzReader,
    )

    _c.authz_reader = SqlAlchemyAuthzReader(db.session, cache_provider=get_reader_cache)

    # The RoleCheckerPort / ICompanyPermissionChecker implementation resolves
    # permissions through the same reader as the route decorators — one
    # authority for use-cases and routes alike.
    if _role_checker is not None and hasattr(_role_checker, "set_authz_reader"):
        _role_checker.set_authz_reader(_c.authz_reader)

    # Same reason: the invitation use-cases resolve `project:invite` themselves.
    for _invitation_usecase in (
        _c.create_invitation_usecase,
        _c.list_invitations_usecase,
        _c.revoke_invitation_usecase,
    ):
        if _invitation_usecase is not None and hasattr(_invitation_usecase, "set_authz_reader"):
            _invitation_usecase.set_authz_reader(_c.authz_reader)

    # Directly adding an existing user to a project also attaches them to the
    # project's company — without an access row they resolve to no permissions.
    if _c.create_invitation_usecase is not None and hasattr(_c.create_invitation_usecase, "set_access_repo"):
        _c.create_invitation_usecase.set_access_repo(_access_repo)

    # D3 day roster use case — needs worker_repository + labor_entry_repository
    # (wired earlier in configure_container) plus the authz reader just above.
    if _c.worker_repository is not None and _c.labor_entry_repository is not None:
        from app.application.labor.get_day_roster_usecase import GetDayRosterUseCase

        _c.get_day_roster_usecase = GetDayRosterUseCase(
            worker_repo=_c.worker_repository,
            entry_repo=_c.labor_entry_repository,
            authz_reader=_c.authz_reader,
        )

    # admin use-cases
    _c.create_company_usecase = _CreateCompanyUseCase(
        company_repo=_company_repo,
        access_repo=_access_repo,
    )
    _c.update_company_usecase = _UpdateCompanyUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
    )
    _c.delete_company_usecase = _DeleteCompanyUseCase(
        company_repo=_company_repo,
        role_checker=_role_checker,
        authz_reader=_c.authz_reader,
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
        # Directory repos are wired further down; injected after construction.
    )
    _c.set_primary_company_usecase = _SetPrimaryCompanyUseCase(
        access_repo=_access_repo,
    )
    _c.set_member_role_usecase = _SetMemberRoleUseCase(
        access_repo=_access_repo,
        role_checker=_role_checker,
    )
    _c.detach_company_usecase = _DetachCompanyUseCase(
        access_repo=_access_repo,
    )
    # Company join code (mobile onboarding): a company admin issues it, anyone with it joins as member.
    from app.application.companies.join_code_usecases import JoinCompanyByCodeUseCase, SetJoinCodeUseCase

    _c.set_join_code_usecase = SetJoinCodeUseCase(company_repo=_company_repo, clock=_clock, role_checker=_role_checker)
    _c.join_company_by_code_usecase = JoinCompanyByCodeUseCase(
        company_repo=_company_repo, access_repo=_access_repo, clock=_clock
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

    # Company-scoped person directory (Phase 2).
    from app.infrastructure.database.repositories.sqlalchemy_company_person_repository import (
        SqlAlchemyCompanyPersonRepository,
    )

    _c.company_person_repo = SqlAlchemyCompanyPersonRepository(db.session)

    # Attach-by-token is built above, before the directory repos exist; give it
    # those repos now so a redeemed invite also produces the profile that makes
    # the new member assignable ("attached ⇒ listed in the directory").
    _c.redeem_invite_token_usecase = _RedeemInviteTokenUseCase(
        token_repo=_token_repo,
        access_repo=_access_repo,
        hasher=_argon2_hasher,
        clock=_clock,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        user_repo=_c.user_repository,
    )

    # Onboarding use cases (Phase 2 slice B): add member by phone, import
    # from another company, company directory, and the derived "new members"
    # notification feed. All need company_person_repo + person_repo, just
    # wired above/below this point respectively.
    from app.application.company_persons import (
        AddMemberByPhoneUseCase as _AddMemberByPhoneUseCase,
        ImportMembersUseCase as _ImportMembersUseCase,
        ListDirectoryUseCase as _ListDirectoryUseCase,
    )
    from app.application.companies import ListNewMembersUseCase as _ListNewMembersUseCase

    _c.add_member_by_phone_usecase = _AddMemberByPhoneUseCase(
        company_repo=_company_repo,
        access_repo=_access_repo,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        user_repo=_c.user_repository,
        role_checker=_role_checker,
    )
    _c.import_members_usecase = _ImportMembersUseCase(
        access_repo=_access_repo,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        role_checker=_role_checker,
    )
    # Re-wire JoinCompanyByCodeUseCase now that person_repo/company_person_repo
    # exist — it is constructed earlier (companies admin/user use-case block,
    # before the Persons DI section) without them.
    from app.application.companies.join_code_usecases import JoinCompanyByCodeUseCase as _JoinCompanyByCodeUseCaseV2

    _c.join_company_by_code_usecase = _JoinCompanyByCodeUseCaseV2(
        company_repo=_company_repo,
        access_repo=_access_repo,
        clock=_clock,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        user_repo=_c.user_repository,
    )

    _c.list_directory_usecase = _ListDirectoryUseCase(
        company_person_repo=_c.company_person_repo,
        person_repo=_person_repo,
        authz_reader=_c.authz_reader,
    )
    _c.list_new_members_usecase = _ListNewMembersUseCase(
        access_repo=_access_repo,
        company_person_repo=_c.company_person_repo,
        person_repo=_person_repo,
        authz_reader=_c.authz_reader,
    )

    # Re-wire boot/detach with the Phase 2 onboarding cleanup collaborators
    # (project assignments + directory profile + join code rotation) now
    # that person_repo/company_person_repo exist — both use cases are
    # constructed earlier (companies admin/user use-case block) without them.
    from app.application.companies.boot_attached_user_usecase import (
        BootAttachedUserUseCase as _BootAttachedUserUseCaseV2,
    )
    from app.application.companies.detach_company_usecase import DetachCompanyUseCase as _DetachCompanyUseCaseV2

    _c.boot_attached_user_usecase = _BootAttachedUserUseCaseV2(
        company_repo=_company_repo,
        access_repo=_access_repo,
        role_checker=_role_checker,
        authz_reader=_c.authz_reader,
        membership_repo=_c.project_membership_repo,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        clock=_clock,
    )
    _c.detach_company_usecase = _DetachCompanyUseCaseV2(
        access_repo=_access_repo,
        authz_reader=_c.authz_reader,
        membership_repo=_c.project_membership_repo,
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
    )

    # Project assignment use cases: admin/manager assign or unassign an
    # existing company member to/from a project. Needs the membership repo
    # (wired earlier in configure_container).
    if _c.project_membership_repo is not None:
        from app.application.projects.assignments import (
            AssignProjectMemberUseCase as _AssignProjectMemberUseCase,
            UnassignProjectMemberUseCase as _UnassignProjectMemberUseCase,
        )

        _c.assign_project_member_usecase = _AssignProjectMemberUseCase(
            authz_reader=_c.authz_reader,
            access_repo=_access_repo,
            membership_repo=_c.project_membership_repo,
            # role="manager" is a company-wide promotion: it goes through the
            # same use case (and guards) as PATCH /companies/<id>/members/<uid>.
            role_setter=_c.set_member_role_usecase,
            db_session=db.session,
        )
        _c.unassign_project_member_usecase = _UnassignProjectMemberUseCase(
            authz_reader=_c.authz_reader,
            access_repo=_access_repo,
            membership_repo=_c.project_membership_repo,
        )

    # LinkPersonOnSignupUseCase only needs company_person_repo/person_repo/
    # access_repo — all wired above regardless of OTP configuration. M7: this
    # is hoisted OUT of the OTP-specific conditional below so an invitation
    # acceptor (AcceptInvitationUseCase) becomes a real company member even
    # on a deployment where phone-OTP sign-up itself is not configured.
    from app.application.company_persons.link_person_on_signup_usecase import (
        LinkPersonOnSignupUseCase as _LinkPersonOnSignupUseCase,
    )

    _link_person_on_signup = _LinkPersonOnSignupUseCase(
        person_repo=_person_repo,
        company_person_repo=_c.company_person_repo,
        access_repo=_access_repo,
    )

    # Re-wire VerifySignupOtpUseCase with sign-up linking now that
    # company_person_repo/person_repo/access_repo exist — it is constructed
    # earlier (OTP DI block, before the companies section) without them.
    if _c.verify_signup_otp_usecase is not None:
        from app.application.usecases.otp_login import VerifySignupOtpUseCase as _VerifySignupOtpUseCaseV2

        _c.verify_signup_otp_usecase = _VerifySignupOtpUseCaseV2(
            _c.user_repository,
            _otp_repo,
            _c.authorization_service,
            _c.token_issuer,
            max_attempts=int(_cfg.get("OTP_MAX_ATTEMPTS", 5)),
            link_person_on_signup=_link_person_on_signup,
        )

    # Re-wire AcceptInvitationUseCase (invitations are the outsider path,
    # Phase 2): the acceptor becomes a `member` of the invited project's
    # company. Constructed earlier (invitations DI block, before the
    # companies section) without these pieces. M7: unconditional on OTP
    # configuration — company attachment on accept must work regardless.
    if _c.accept_invitation_usecase is not None:
        from app.application.invitations.accept_invitation_usecase import (
            AcceptInvitationUseCase as _AcceptInvitationUseCaseV2,
        )

        _c.accept_invitation_usecase = _AcceptInvitationUseCaseV2(
            invitation_repo=_c.invitation_repo,
            user_repo=_c.user_repository,
            project_membership_repo=_c.project_membership_repo,
            token_issuer=_c.token_issuer,
            db_session=db.session,
            authz_reader=_c.authz_reader,
            access_repo=_access_repo,
            link_person_on_signup=_link_person_on_signup,
            person_repo=_person_repo,
            company_person_repo=_c.company_person_repo,
            otp_repo=_c.login_otp_repository,
            otp_max_attempts=int(_cfg.get("OTP_MAX_ATTEMPTS", 5)),
        )

    # Re-wire CreateWorkerUseCase with person_repo now that the latter
    # exists. configure_container() in wiring.py wires it with the worker
    # repo only — cook 1d-ii-b adds the inline-Person-create branch which
    # needs the Person repo too. The existing-Person-link branch works
    # without it, so this rewire is strictly additive.
    if _c.worker_repository and _c.create_worker_usecase is not None:
        from app.application.labor.create_worker import (
            CreateWorkerUseCase as _CreateWorkerUseCase,
        )

        _c.create_worker_usecase = _CreateWorkerUseCase(
            worker_repo=_c.worker_repository,
            person_repo=_person_repo,
            company_person_repo=_c.company_person_repo,
            authz_reader=_c.authz_reader,
        )

    # -----------------------------------------------------------------------
    # Labor roles DI wiring
    # -----------------------------------------------------------------------
    from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
    from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase as _CreateLaborRoleUseCase
    from app.application.labor.update_labor_role_usecase import UpdateLaborRoleUseCase as _UpdateLaborRoleUseCase
    from app.application.labor.delete_labor_role_usecase import DeleteLaborRoleUseCase as _DeleteLaborRoleUseCase
    from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase as _ListLaborRolesUseCase

    _labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
    _c.labor_role_repository = _labor_role_repo
    _c.create_labor_role_usecase = _CreateLaborRoleUseCase(repo=_labor_role_repo, db_session=db.session)
    _c.update_labor_role_usecase = _UpdateLaborRoleUseCase(repo=_labor_role_repo, db_session=db.session)
    _c.delete_labor_role_usecase = _DeleteLaborRoleUseCase(repo=_labor_role_repo, db_session=db.session)
    _c.list_labor_roles_usecase = _ListLaborRolesUseCase(repo=_labor_role_repo)

    # Default role roster for a newly created company (Phase 2 onboarding
    # slice wires this into company creation; exposed here so seeds/tests
    # can call it directly).
    from app.application.labor.seed_default_labor_roles import SeedDefaultLaborRolesUseCase as _SeedDefaultLRUC

    _c.seed_default_labor_roles_usecase = _SeedDefaultLRUC(repo=_labor_role_repo, db_session=db.session)

    # Company member pay defaults — wired here, after the labor-role repo exists,
    # because it validates that an assigned role belongs to the caller's company.
    from app.application.company_persons import (
        UpdateMemberPayDefaultsUseCase as _UpdateMemberPayDefaultsUseCase,
    )

    _c.update_member_pay_defaults_usecase = _UpdateMemberPayDefaultsUseCase(
        company_person_repo=_c.company_person_repo,
        labor_role_repo=_labor_role_repo,
        access_repo=_access_repo,
        role_checker=_role_checker,
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
        ListProjectBillingDocumentsUseCase,
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
        access_repo=_access_repo,  # company-admin may manage company billing
    )
    from app.infrastructure.adapters.funds_release_adapter import FundsReleaseAdapter

    _funds_release_adapter = FundsReleaseAdapter(invoice_repo=_c.invoice_repository) if _c.invoice_repository else None
    _c.update_billing_document_status_usecase = UpdateBillingDocumentStatusUseCase(
        doc_repo=_billing_doc_repo,
        funds_release=_funds_release_adapter,
        access_repo=_access_repo,  # company-admin may manage company billing
    )
    _c.list_billing_documents_usecase = ListBillingDocumentsUseCase(
        doc_repo=_billing_doc_repo,
        project_repo=_project_repo,  # H1 — project:read authorization
        access_repo=_access_repo,  # per-company admin scoping for visibility
    )
    _c.get_billing_document_usecase = GetBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
    )
    _c.delete_billing_document_usecase = DeleteBillingDocumentUseCase(
        doc_repo=_billing_doc_repo,
        access_repo=_access_repo,  # company-admin may manage company billing
    )
    _c.render_billing_document_pdf_usecase = RenderBillingDocumentPdfUseCase(
        doc_repo=_billing_doc_repo,
        pdf_renderer=_billing_pdf_renderer,
        access_repo=_access_repo,  # company-admin may render company billing
    )
    _c.render_billing_document_xlsx_usecase = RenderBillingDocumentXlsxUseCase(
        doc_repo=_billing_doc_repo,
        xlsx_renderer=_billing_xlsx_renderer,
        access_repo=_access_repo,  # company-admin may render company billing
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
    _c.list_project_billing_documents_usecase = ListProjectBillingDocumentsUseCase(
        doc_repo=_billing_doc_repo,
        project_repo=_project_repo,  # project:read authorization
    )

    # Re-wire materials-expenses use-cases with the now-available access_repo
    # (configure_container wired them with access_repo=None as a placeholder)
    from app.application.invoice.list_materials_expenses_usecase import (
        ListMaterialsExpensesUseCase as _ListMaterialsExpensesUseCase,
    )
    from app.application.invoice.set_refundable_status_usecase import (
        SetInvoiceRefundableStatusUseCase as _SetRefundableStatusUseCase,
    )

    if _c.invoice_repository is not None:
        _c.list_materials_expenses_usecase = _ListMaterialsExpensesUseCase(
            invoice_repo=_c.invoice_repository,
            access_repo=_access_repo,
        )
        _c.set_refundable_status_usecase = _SetRefundableStatusUseCase(
            invoice_repo=_c.invoice_repository,
            access_repo=_access_repo,
            funds_release=_funds_release_adapter,
        )

    # -----------------------------------------------------------------------
    # Payment methods DI wiring (invoice-payment-method feature)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
        SqlAlchemyPaymentMethodRepository,
    )
    from app.application.payment_methods.list_payment_methods_usecase import (
        ListPaymentMethodsUseCase as _ListPaymentMethodsUseCase,
    )
    from app.application.payment_methods.create_payment_method_usecase import (
        CreatePaymentMethodUseCase as _CreatePaymentMethodUseCase,
    )
    from app.application.payment_methods.update_payment_method_usecase import (
        UpdatePaymentMethodUseCase as _UpdatePaymentMethodUseCase,
    )
    from app.application.payment_methods.delete_payment_method_usecase import (
        DeletePaymentMethodUseCase as _DeletePaymentMethodUseCase,
    )
    from app.application.payment_methods.seed_payment_methods_for_company_usecase import (
        SeedPaymentMethodsForCompanyUseCase as _SeedPaymentMethodsUseCase,
    )

    _pm_repo = SqlAlchemyPaymentMethodRepository(db.session)
    _c.payment_method_repo = _pm_repo

    _c.list_payment_methods_usecase = _ListPaymentMethodsUseCase(
        payment_method_repo=_pm_repo,
        role_checker=_role_checker,
        access_repo=_access_repo,
        company_repo=_company_repo,
    )
    _c.create_payment_method_usecase = _CreatePaymentMethodUseCase(
        payment_method_repo=_pm_repo,
        role_checker=_role_checker,
    )
    _c.update_payment_method_usecase = _UpdatePaymentMethodUseCase(
        payment_method_repo=_pm_repo,
        role_checker=_role_checker,
    )
    _c.delete_payment_method_usecase = _DeletePaymentMethodUseCase(
        payment_method_repo=_pm_repo,
        role_checker=_role_checker,
    )
    _c.seed_payment_methods_usecase = _SeedPaymentMethodsUseCase(
        payment_method_repo=_pm_repo,
    )

    # Note: invoice write use-cases (create_invoice_usecase, update_invoice_usecase) are
    # constructed once below, after the worker reader they depend on exists.

    # Wire seeder into create_company_usecase (phase 05)
    from app.application.companies.create_company_usecase import (
        CreateCompanyUseCase as _CreateCompanyUseCaseV2,
    )

    _c.create_company_usecase = _CreateCompanyUseCaseV2(
        company_repo=_company_repo,
        access_repo=_access_repo,
        seed_payment_methods=_c.seed_payment_methods_usecase,
        seed_default_labor_roles=_c.seed_default_labor_roles_usecase,
        person_repo=_c.person_repo,
        company_person_repo=_c.company_person_repo,
        user_repo=_c.user_repository,
    )

    # -----------------------------------------------------------------------
    # Project documents DI wiring (phase 03)
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_project_document_repository import (
        SqlAlchemyProjectDocumentRepository,
    )
    from app.infrastructure.adapters.werkzeug_filename_sanitizer import WerkzeugFilenameSanitizer
    from app.application.project_documents import (
        UploadProjectDocumentUseCase,
        ListProjectDocumentsUseCase,
        GetProjectDocumentUseCase,
        DeleteProjectDocumentUseCase,
        RenameProjectDocumentUseCase,
        PurgeSoftDeletedDocumentsUseCase,
    )

    _doc_repo = SqlAlchemyProjectDocumentRepository(db.session)
    _c.project_document_repository = _doc_repo
    # Reuse the same S3AttachmentStorage singleton — structurally satisfies IDocumentStorage
    _c.document_storage = storage
    _filename_sanitizer = WerkzeugFilenameSanitizer()

    _c.upload_project_document_usecase = UploadProjectDocumentUseCase(
        repo=_doc_repo,
        storage=storage,
        db_session=db.session,
        filename_sanitizer=_filename_sanitizer,
    )
    _c.list_project_documents_usecase = ListProjectDocumentsUseCase(repo=_doc_repo)
    _c.get_project_document_usecase = GetProjectDocumentUseCase(repo=_doc_repo, storage=storage)
    _c.delete_project_document_usecase = DeleteProjectDocumentUseCase(
        repo=_doc_repo,
        db_session=db.session,
    )
    _c.rename_project_document_usecase = RenameProjectDocumentUseCase(
        repo=_doc_repo,
        db_session=db.session,
    )
    _c.purge_soft_deleted_documents_usecase = PurgeSoftDeletedDocumentsUseCase(
        repo=_doc_repo,
        storage=storage,
        db_session=db.session,
    )

    # Presigned upload use cases — only wired when S3_PUBLIC_ENDPOINT_URL is set
    if storage.presigned_uploads_enabled:
        from app.application.project_documents.presign_project_document_upload import (
            PresignProjectDocumentUploadUseCase,
        )
        from app.application.project_documents.confirm_project_document_upload import (
            ConfirmProjectDocumentUploadUseCase,
        )

        _c.presign_project_document_usecase = PresignProjectDocumentUploadUseCase(
            storage=storage,
            filename_sanitizer=_filename_sanitizer,
        )
        _c.confirm_project_document_usecase = ConfirmProjectDocumentUploadUseCase(
            repo=_doc_repo,
            storage=storage,
            db_session=db.session,
        )

    # -----------------------------------------------------------------------
    # Project photos DI wiring
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_project_photo_repository import (
        SqlAlchemyProjectPhotoRepository,
    )
    from app.infrastructure.adapters.pillow_image_thumbnailer import PillowImageThumbnailer
    from app.infrastructure.adapters.ffmpeg_video_thumbnailer import FfmpegVideoThumbnailer
    from app.infrastructure.adapters.media_thumbnailer import MediaThumbnailer
    from app.application.project_photos import (
        UploadProjectPhotoUseCase,
        ListProjectPhotosUseCase,
        GetProjectPhotoUseCase,
        UpdateProjectPhotoUseCase,
        DeleteProjectPhotoUseCase,
    )

    _photo_repo = SqlAlchemyProjectPhotoRepository(db.session)
    _c.project_photo_repository = _photo_repo
    # Dispatch thumbnailing by media kind: images via Pillow, videos via ffmpeg poster frame.
    _photo_thumbnailer = MediaThumbnailer(
        image_thumbnailer=PillowImageThumbnailer(),
        video_thumbnailer=FfmpegVideoThumbnailer(),
    )

    # Reuse document_storage singleton (S3AttachmentStorage satisfies IDocumentStorage).
    # Reuse _filename_sanitizer already instantiated above for project documents.
    _c.upload_project_photo_usecase = UploadProjectPhotoUseCase(
        repo=_photo_repo,
        storage=storage,
        thumbnailer=_photo_thumbnailer,
        db_session=db.session,
        filename_sanitizer=_filename_sanitizer,
    )
    _c.list_project_photos_usecase = ListProjectPhotosUseCase(repo=_photo_repo)
    _c.get_project_photo_usecase = GetProjectPhotoUseCase(repo=_photo_repo, storage=storage)
    _c.update_project_photo_usecase = UpdateProjectPhotoUseCase(
        repo=_photo_repo,
        db_session=db.session,
    )
    _c.delete_project_photo_usecase = DeleteProjectPhotoUseCase(
        repo=_photo_repo,
        db_session=db.session,
    )

    # -----------------------------------------------------------------------
    # Bibliotheque DI wiring
    # -----------------------------------------------------------------------
    from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_supplier_repository import (
        SqlAlchemyBibliothequeSupplierRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_product_repository import (
        SqlAlchemyBibliothequeProductRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_purchase_repository import (
        SqlAlchemyBibliothequePurchaseRepository,
    )
    from app.infrastructure.adapters.bibliotheque_image_storage import BibliothequeImageStorage
    from app.infrastructure.adapters.company_membership_reader import CompanyMembershipReader
    from app.application.bibliotheque.list_suppliers_usecase import ListSuppliersUseCase as _ListSuppliersUC
    from app.application.bibliotheque.list_categories_usecase import ListCategoriesUseCase as _ListCategoriesUC
    from app.application.bibliotheque.list_products_usecase import ListProductsUseCase as _ListProductsUC
    from app.application.bibliotheque.get_product_usecase import GetProductUseCase as _GetProductUC
    from app.application.bibliotheque.get_product_image_usecase import GetProductImageUseCase as _GetProductImageUC
    from app.application.bibliotheque.import_purchases_usecase import ImportPurchasesUseCase as _ImportPurchasesUC
    from app.application.bibliotheque.update_product_usecase import UpdateProductUseCase as _UpdateProductUC
    from app.application.bibliotheque.create_product_usecase import CreateProductUseCase as _CreateProductUC
    from app.application.bibliotheque.delete_product_usecase import DeleteProductUseCase as _DeleteProductUC
    from app.application.bibliotheque.upload_product_image_usecase import (
        UploadProductImageUseCase as _UploadProductImageUC,
    )
    from app.application.bibliotheque.fetch_product_image_from_url_usecase import (
        FetchProductImageFromUrlUseCase as _FetchProductImageFromUrlUC,
    )

    _biblio_supplier_repo = SqlAlchemyBibliothequeSupplierRepository(db.session)
    _biblio_product_repo = SqlAlchemyBibliothequeProductRepository(db.session)
    _biblio_purchase_repo = SqlAlchemyBibliothequePurchaseRepository(db.session)
    _biblio_image_storage = BibliothequeImageStorage(
        endpoint_url=Config.S3_ENDPOINT_URL,
        access_key=Config.S3_ACCESS_KEY,
        secret_key=Config.S3_SECRET_KEY,
        bucket=Config.S3_BUCKET,
        region=Config.S3_REGION,
    )
    # Reuse existing user_company_access_repo as the membership reader
    _biblio_membership_reader = CompanyMembershipReader(_access_repo)
    # Reuse existing authorization_service as the permission checker
    _biblio_permission_checker = _role_checker

    _c.bibliotheque_supplier_repo = _biblio_supplier_repo
    _c.bibliotheque_product_repo = _biblio_product_repo
    _c.bibliotheque_purchase_repo = _biblio_purchase_repo
    _c.bibliotheque_image_storage = _biblio_image_storage
    _c.bibliotheque_membership_reader = _biblio_membership_reader

    _c.bibliotheque_list_suppliers_usecase = _ListSuppliersUC(
        supplier_repo=_biblio_supplier_repo,
        membership_reader=_biblio_membership_reader,
    )
    _c.bibliotheque_list_categories_usecase = _ListCategoriesUC(
        product_repo=_biblio_product_repo,
        membership_reader=_biblio_membership_reader,
    )
    _c.bibliotheque_list_products_usecase = _ListProductsUC(
        product_repo=_biblio_product_repo,
        membership_reader=_biblio_membership_reader,
    )
    _c.bibliotheque_get_product_usecase = _GetProductUC(
        product_repo=_biblio_product_repo,
        purchase_repo=_biblio_purchase_repo,
        membership_reader=_biblio_membership_reader,
    )
    _c.bibliotheque_get_product_image_usecase = _GetProductImageUC(
        product_repo=_biblio_product_repo,
        image_storage=_biblio_image_storage,
        membership_reader=_biblio_membership_reader,
    )
    _c.bibliotheque_import_usecase = _ImportPurchasesUC(
        supplier_repo=_biblio_supplier_repo,
        product_repo=_biblio_product_repo,
        purchase_repo=_biblio_purchase_repo,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )
    _c.bibliotheque_update_product_usecase = _UpdateProductUC(
        product_repo=_biblio_product_repo,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )
    _c.bibliotheque_create_product_usecase = _CreateProductUC(
        supplier_repo=_biblio_supplier_repo,
        product_repo=_biblio_product_repo,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )
    _c.bibliotheque_delete_product_usecase = _DeleteProductUC(
        product_repo=_biblio_product_repo,
        image_storage=_biblio_image_storage,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )
    _c.bibliotheque_upload_image_usecase = _UploadProductImageUC(
        product_repo=_biblio_product_repo,
        image_storage=_biblio_image_storage,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )
    _c.bibliotheque_fetch_image_from_url_usecase = _FetchProductImageFromUrlUC(
        product_repo=_biblio_product_repo,
        image_storage=_biblio_image_storage,
        membership_reader=_biblio_membership_reader,
        permission_checker=_biblio_permission_checker,
        db_session=db.session,
    )

    # -----------------------------------------------------------------------
    # Labor write use-cases — single construction point.
    # -----------------------------------------------------------------------
    from app.application.labor.log_attendance import LogAttendanceUseCase as _LogAttendUC
    from app.application.labor.update_attendance import UpdateAttendanceUseCase as _UpdateAttendUC
    from app.application.labor.bulk_log_attendance import BulkLogAttendanceUseCase as _BulkLogUC

    if _c.worker_repository is not None and _c.labor_entry_repository is not None:
        _c.log_attendance_usecase = _LogAttendUC(
            worker_repo=_c.worker_repository,
            entry_repo=_c.labor_entry_repository,
        )
        _c.update_attendance_usecase = _UpdateAttendUC(
            entry_repo=_c.labor_entry_repository,
            worker_repo=_c.worker_repository,
        )
        _c.bulk_log_attendance_usecase = _BulkLogUC(
            worker_repo=_c.worker_repository,
            entry_repo=_c.labor_entry_repository,
            db_session=db.session,
        )

    # Read-only worker lookup for validating/snapshotting invoice worker links —
    # decouples the invoice BC from labor's WorkerModel/IWorkerRepository.
    from app.infrastructure.adapters.sqlalchemy_worker_reader import (
        SQLAlchemyWorkerReader as _WorkerReader,
    )

    _worker_reader = _WorkerReader(db.session)
    _c.worker_reader = _worker_reader

    # Single construction of invoice write use-cases — includes the worker reader from the start.
    from app.application.invoice.create_invoice import CreateInvoiceUseCase as _CreateInvUC
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase as _UpdateInvUC

    if _c.invoice_repository is not None:
        _pm = _c.payment_method_repo
        _c.create_invoice_usecase = _CreateInvUC(
            invoice_repo=_c.invoice_repository,
            payment_method_repo=_pm,
            worker_reader=_worker_reader,
        )
        _c.update_invoice_usecase = _UpdateInvUC(
            invoice_repo=_c.invoice_repository,
            payment_method_repo=_pm,
            worker_reader=_worker_reader,
        )

    # -----------------------------------------------------------------------
    # Worker rate-change repo + use-cases (effective-dated pay-rate timeline)
    # CRITICAL: any use-case added here MUST also appear in the invitation_app
    # fixture in tests/conftest.py or the fixture will drift from prod wiring.
    # -----------------------------------------------------------------------
    from app.infrastructure.adapters.sqlalchemy_worker_rate_change import (
        SQLAlchemyWorkerRateChangeRepository as _WorkerRateChangeRepo,
    )
    from app.application.labor.set_worker_rate_change import SetWorkerRateChangeUseCase as _SetRateUC
    from app.application.labor.list_worker_rate_changes import ListWorkerRateChangesUseCase as _ListRateUC
    from app.application.labor.delete_worker_rate_change import DeleteWorkerRateChangeUseCase as _DelRateUC

    _rate_change_repo = _WorkerRateChangeRepo(db.session)
    _c.worker_rate_change_repository = _rate_change_repo

    if _c.worker_repository is not None:
        _c.set_worker_rate_change_usecase = _SetRateUC(
            worker_repo=_c.worker_repository,
            rate_change_repo=_rate_change_repo,
        )
        _c.list_worker_rate_changes_usecase = _ListRateUC(
            worker_repo=_c.worker_repository,
            rate_change_repo=_rate_change_repo,
        )
        _c.delete_worker_rate_change_usecase = _DelRateUC(
            worker_repo=_c.worker_repository,
            rate_change_repo=_rate_change_repo,
        )

    # Re-wire list_labor_entries_usecase with the rate-change repo now that it exists.
    # configure_container() wired it with rate_change_repo=None (repo not built yet).
    if _c.worker_repository is not None and _c.labor_entry_repository is not None:
        from app.application.labor.list_labor_entries import ListLaborEntriesUseCase as _ListEntriesUC

        _c.list_labor_entries_usecase = _ListEntriesUC(
            worker_repo=_c.worker_repository,
            entry_repo=_c.labor_entry_repository,
            rate_change_repo=_rate_change_repo,
        )

    # The export holds its OWN entry lister, built in configure_container() before the
    # rate repo existed, so re-wiring the container's copy above does not reach it.
    # Without this the exported day costs stay on each worker's base rate while every
    # other view honors the timeline. The rate repo also feeds the export header rate.
    if _c.export_labor_usecase is not None:
        if _c.list_labor_entries_usecase is not None:
            _c.export_labor_usecase._list_entries_usecase = _c.list_labor_entries_usecase
        _c.export_labor_usecase._rate_change_repo = _rate_change_repo

    # Re-wire list_workers_usecase with the rate-change repo so the worker list
    # returns current_daily_rate resolved from the effective-dated timeline.
    if _c.worker_repository is not None:
        from app.application.labor.list_workers import ListWorkersUseCase as _ListWorkersUC

        _c.list_workers_usecase = _ListWorkersUC(
            worker_repo=_c.worker_repository,
            rate_change_repo=_rate_change_repo,
        )
