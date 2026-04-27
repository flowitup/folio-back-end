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
        jwt_secret = getattr(config_class, "JWT_SECRET_KEY", "")
        if not jwt_secret or "dev-" in jwt_secret.lower():
            raise RuntimeError("CRITICAL: JWT_SECRET_KEY must be set in production.")
        secret_key = getattr(config_class, "SECRET_KEY", "")
        if not secret_key or "dev-" in secret_key.lower():
            raise RuntimeError("CRITICAL: SECRET_KEY must be set in production.")

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Reject upload bodies > 10 MB at the WSGI layer (matches attachment use-case cap)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    # Initialize extensions
    cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")]
    CORS(app, supports_credentials=True, origins=cors_origins)
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
    from app.api.v1.invoices import invoice_bp
    from app.api.v1.tasks import task_bp
    from app.api.v1.invitations import invitations_bp
    from app.api.v1.roles import roles_bp

    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/v1/projects")
    app.register_blueprint(labor_bp, url_prefix="/api/v1")
    app.register_blueprint(invoice_bp, url_prefix="/api/v1")
    app.register_blueprint(task_bp, url_prefix="/api/v1")
    app.register_blueprint(invitations_bp, url_prefix="/api/v1/invitations")
    app.register_blueprint(roles_bp, url_prefix="/api/v1/roles")

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
