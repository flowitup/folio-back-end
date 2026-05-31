"""
Flask blueprint that serves the OpenAPI JSON spec and mounts Swagger UI.

Routes registered:
  GET /openapi.json       — returns the full OpenAPI 3.0 spec as JSON
  GET /v1/documentation/  — Swagger UI pointing at /openapi.json
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from flask_swagger_ui import get_swaggerui_blueprint

# Blueprint that owns /openapi.json
openapi_bp = Blueprint("openapi", __name__)

# Swagger UI mounted at /v1/documentation, consuming /openapi.json
_SWAGGER_URL = "/v1/documentation"
_API_URL = "/openapi.json"

swaggerui_bp = get_swaggerui_blueprint(
    _SWAGGER_URL,
    _API_URL,
    config={"app_name": "Folio API"},
)


@openapi_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    """
    Serve the auto-generated OpenAPI 3.1.0 spec.

    Built lazily on each request so that registration order of blueprints
    is never a concern — all routes are already registered by the time
    any client hits this endpoint.
    """
    from app.api.openapi.generator import build_spec

    try:
        spec_dict = build_spec(current_app._get_current_object())  # type: ignore[attr-defined]
        return jsonify(spec_dict)
    except Exception as e:
        current_app.logger.exception("OpenAPI spec generation failed")
        return jsonify({"error": "spec_generation_failed", "message": str(e)}), 500
