"""
Flask-RESTX Swagger API Configuration

Provides Swagger UI documentation at /v1/documentation with interactive API explorer.
Models defined here are for documentation purposes only - actual routes use Flask blueprints.
"""

from flask import Flask
from flask_restx import Api

from app.api.swagger_models import create_all_models


def init_swagger(app: Flask) -> None:
    """Initialize Swagger API documentation on the Flask app."""
    api = Api(
        title="Construction Management API",
        version="0.2.0",
        description="Backend API for Construction Management System",
        doc="/v1/documentation",
        authorizations={
            "Bearer": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "JWT Bearer token. Format: 'Bearer {token}'",
            }
        },
        security="Bearer",
    )

    auth_ns = api.namespace("auth", description="Authentication operations", path="/api/v1/auth")
    projects_ns = api.namespace("projects", description="Project management operations", path="/api/v1/projects")
    models = create_all_models(api)

    from app.api.swagger_resources import register_resources, register_project_resources

    register_resources(auth_ns, models)
    register_project_resources(projects_ns, models)

    api.init_app(app)
