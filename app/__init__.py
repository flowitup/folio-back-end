"""
Construction Backend Application Factory

This module provides the create_app() function which sets up and configures
the Flask application following the application factory pattern.
"""

from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from config import Config

# Import our custom Base before creating SQLAlchemy instance
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

    # Production security check: Ensure JWT secret is not default
    import os
    if os.environ.get("FLASK_ENV") == "production":
        jwt_secret = getattr(config_class, "JWT_SECRET_KEY", "")
        if not jwt_secret or "dev-" in jwt_secret.lower():
            raise RuntimeError(
                "CRITICAL: JWT_SECRET_KEY must be set to a secure value in production. "
                "Set JWT_SECRET_KEY environment variable."
            )

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize extensions
    CORS(app)
    db.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)

    # Initialize migrations
    migrate.init_app(app, db, render_as_batch=True)

    # JWT error handlers
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({
            "error": "TokenExpired",
            "message": "Token has expired",
            "status_code": 401
        }), 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({
            "error": "InvalidToken",
            "message": "Token is invalid",
            "status_code": 401
        }), 401

    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({
            "error": "Unauthorized",
            "message": "Missing authentication token",
            "status_code": 401
        }), 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({
            "error": "TokenRevoked",
            "message": "Token has been revoked",
            "status_code": 401
        }), 401

    # Token revocation check
    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        jti = jwt_payload.get("jti")
        from wiring import get_container
        container = get_container()
        if container.token_issuer:
            return container.token_issuer.is_token_revoked(jti)
        return False

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health_check():
        """Health check endpoint for monitoring."""
        return jsonify({"status": "ok"})

    # Register blueprints
    from app.api.v1 import bp as api_v1_bp
    from app.api.v1.auth import auth_bp
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")

    return app
