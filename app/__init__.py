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

    # Production security check
    if os.environ.get("FLASK_ENV") == "production":
        jwt_secret = getattr(config_class, "JWT_SECRET_KEY", "")
        if not jwt_secret or "dev-" in jwt_secret.lower():
            raise RuntimeError("CRITICAL: JWT_SECRET_KEY must be set in production.")

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize extensions
    CORS(app)
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
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/v1/projects")

    # Initialize Swagger API documentation
    from app.api.swagger import init_swagger
    init_swagger(app)

    return app


def _configure_di_container() -> None:
    """Configure the dependency injection container."""
    from wiring import configure_container
    from app.infrastructure.adapters.sqlalchemy_user_repository import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project_repository import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.argon2_password_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_token_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session_manager import FlaskSessionManager
    from config import Config

    configure_container(
        user_repository=SQLAlchemyUserRepository(db.session),
        project_repository=SQLAlchemyProjectRepository(db.session),
        password_hasher=Argon2PasswordHasher(),
        token_issuer=JWTTokenIssuer(redis_url=Config.REDIS_URL),
        session_manager=FlaskSessionManager(),
    )
