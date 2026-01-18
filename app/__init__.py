"""
Construction Backend Application Factory

This module provides the create_app() function which sets up and configures
the Flask application following the application factory pattern.
"""

from flask import Flask, jsonify
from flask_cors import CORS
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from config import Config

# Import our custom Base before creating SQLAlchemy instance
from app.infrastructure.database.models import Base

# Create SQLAlchemy with our custom Base's metadata
db = SQLAlchemy(model_class=Base)
migrate = Migrate()


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

    # Configure SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config_class.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize extensions
    CORS(app)
    db.init_app(app)

    # Initialize migrations
    migrate.init_app(app, db, render_as_batch=True)

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health_check():
        """Health check endpoint for monitoring."""
        return jsonify({"status": "ok"})

    # Register blueprints
    from app.api.v1 import bp as api_v1_bp
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")

    return app
