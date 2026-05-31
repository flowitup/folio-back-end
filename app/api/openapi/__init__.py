"""
OpenAPI documentation package.

Provides:
  - ``init_openapi(app)``  — register spec + Swagger UI blueprints
  - ``openapi_doc(...)``   — decorator to annotate route handlers (re-exported)
"""

from flask import Flask

from app.api.openapi.decorator import openapi_doc
from app.api.openapi.ui import openapi_bp, swaggerui_bp


def init_openapi(app: Flask) -> None:
    """
    Register the OpenAPI spec endpoint and Swagger UI on *app*.

    Called from the application factory under the same gate condition
    as the previous swagger layer: disabled in production unless
    EXPOSE_DOCS=1 is set.
    """
    app.register_blueprint(openapi_bp)
    app.register_blueprint(swaggerui_bp)


__all__ = ["init_openapi", "openapi_doc"]
