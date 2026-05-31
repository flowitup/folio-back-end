"""
OpenAPI 3.1.0 spec generator.

Walks Flask's url_map to discover all registered routes, enriches each
operation with ``_openapi_meta`` data written by ``@openapi_doc``, and
returns the full spec as a plain dict via ``build_spec(app)``.
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Flask

from app.api.openapi.path_utils import flask_path_to_openapi

# Re-export under the private name so existing imports (e.g. tests) continue
# to work without modification.
_flask_path_to_openapi = flask_path_to_openapi

# Endpoints that are publicly accessible — no bearerAuth required.
_PUBLIC_ENDPOINTS: frozenset[str] = frozenset({"auth.login", "auth.refresh"})


def _resolve_version() -> str:
    """Read package version; fall back to '0.0.0' if not installed."""
    try:
        from importlib.metadata import version

        return version("construction-backend")
    except Exception:
        return "0.0.0"


def _build_operation(
    endpoint: str,
    methods: set[str],
    path_params: list[dict[str, Any]],
    meta: Optional[dict[str, Any]],
    spec: Any,
) -> dict[str, dict[str, Any]]:
    """
    Build a dict of {http_method: operation_object} for one URL rule.

    If *meta* is None (no ``@openapi_doc`` decorator), a minimal operation
    with just path params, a default 200 response, and security is produced.
    """
    from app.api.openapi.pydantic_schema import register_model

    # Determine tags from meta or derive from blueprint name.
    tags: list[str]
    if meta and meta.get("tags"):
        tags = meta["tags"]
    else:
        tags = [endpoint.split(".")[0]]

    operation_base: dict[str, Any] = {"tags": tags}

    if meta:
        if meta.get("summary"):
            operation_base["summary"] = meta["summary"]
        if meta.get("description"):
            operation_base["description"] = meta["description"]

    # Path parameters (shared across all methods on this path).
    if path_params:
        operation_base["parameters"] = list(path_params)

    # Query parameters from a Pydantic model's properties.
    if meta and meta.get("query"):
        query_model = meta["query"]
        q_schema = query_model.model_json_schema()
        q_props = q_schema.get("properties", {})
        q_required = set(q_schema.get("required", []))
        query_params = operation_base.setdefault("parameters", [])
        for prop_name, prop_schema in q_props.items():
            query_params.append(
                {
                    "in": "query",
                    "name": prop_name,
                    "required": prop_name in q_required,
                    "schema": prop_schema,
                }
            )

    # Request body.
    if meta and meta.get("request"):
        ref = register_model(spec, meta["request"])
        operation_base["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": ref}}},
        }

    # Responses.
    responses: dict[str, Any]
    if meta and meta.get("responses"):
        responses = {}
        for status_code, resp_model in meta["responses"].items():
            ref = register_model(spec, resp_model)
            responses[str(status_code)] = {
                "description": _http_status_description(int(status_code)),
                "content": {"application/json": {"schema": {"$ref": ref}}},
            }
    else:
        responses = {"200": {"description": "Success"}}
    operation_base["responses"] = responses

    # Security: omit bearer requirement for explicitly public endpoints.
    is_public = endpoint in _PUBLIC_ENDPOINTS
    if meta is not None:
        is_public = is_public or (meta.get("auth") is False)
    if not is_public:
        operation_base["security"] = [{"bearerAuth": []}]

    return {method.lower(): dict(operation_base) for method in methods}


def _http_status_description(code: int) -> str:
    """Return a short human-readable label for common HTTP status codes."""
    _labels = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
        500: "Internal Server Error",
    }
    return _labels.get(code, "Response")


def build_spec(app: Flask) -> dict[str, Any]:
    """
    Build the complete OpenAPI 3.1.0 spec dict for *app*.

    Iterates Flask's url_map, enriches each route with ``_openapi_meta``
    when present, and returns ``spec.to_dict()``.
    """
    from apispec import APISpec

    spec = APISpec(
        title="Folio API",
        version=_resolve_version(),
        openapi_version="3.1.0",
        plugins=[],
    )

    # JWT bearer security scheme — used by all non-public endpoints.
    spec.components.security_scheme(
        "bearerAuth",
        {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    )

    # Accumulate all operations per OpenAPI path before registering.
    # Multiple Flask url_map rules can map to the same OpenAPI path (e.g.
    # separate rules for GET and POST on the same resource URL). We merge
    # their operations into one dict so spec.path() is called exactly once
    # per unique path — preventing later rules from silently overwriting
    # earlier ones.
    path_operations: dict[str, dict[str, Any]] = {}

    for rule in app.url_map.iter_rules():
        # Skip Flask internals and our own docs endpoints.
        if rule.endpoint == "static":
            continue
        if rule.endpoint.startswith("openapi.") or rule.endpoint.startswith("swagger_ui."):
            continue

        rule_str = rule.rule
        # Keep only /api/* and /health routes.
        if not (rule_str.startswith("/api") or rule_str == "/health" or rule_str.startswith("/health")):
            continue

        methods = rule.methods - {"HEAD", "OPTIONS"}  # type: ignore[operator]
        if not methods:
            continue

        openapi_path, path_params = flask_path_to_openapi(rule_str)

        # Resolve view function and optional meta.
        view_func = app.view_functions.get(rule.endpoint)
        meta: Optional[dict[str, Any]] = None
        if view_func is not None:
            meta = getattr(view_func, "_openapi_meta", None)

        operations = _build_operation(rule.endpoint, methods, path_params, meta, spec)

        # Merge operations onto the same path (e.g. GET + POST share a path entry).
        if openapi_path in path_operations:
            path_operations[openapi_path].update(operations)
        else:
            path_operations[openapi_path] = operations

    for path, operations in path_operations.items():
        spec.path(path=path, operations=operations)

    return spec.to_dict()
