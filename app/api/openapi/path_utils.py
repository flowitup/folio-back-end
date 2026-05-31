"""
Flask URL rule → OpenAPI path conversion utilities.

Responsible for:
- Converting Flask ``<converter:name>`` path variables to ``{name}`` OpenAPI syntax.
- Extracting OpenAPI parameter objects (in: path) from those variables.
- Mapping Flask converter types to OpenAPI primitive types.
"""

from __future__ import annotations

import re
from typing import Any

# Flask converter type → OpenAPI primitive type mapping.
_CONVERTER_TYPE_MAP: dict[str, str] = {
    "int": "integer",
    "float": "number",
    "uuid": "string",
    "path": "string",
    "string": "string",
    "default": "string",
}

# Regex to extract Flask path variables: <converter:name> or <name>
_PATH_VAR_RE = re.compile(r"<(?:([a-z_]+):)?([a-zA-Z_][a-zA-Z0-9_]*)>")


def flask_path_to_openapi(rule_str: str) -> tuple[str, list[dict[str, Any]]]:
    """
    Convert a Flask URL rule string to an OpenAPI path string and path params.

    Flask:   /api/v1/projects/<int:project_id>/tasks/<task_id>
    OpenAPI: /api/v1/projects/{project_id}/tasks/{task_id}

    Returns:
        (openapi_path, list_of_parameter_objects)
    """
    params: list[dict[str, Any]] = []

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        converter = match.group(1) or "default"
        param_name = match.group(2)
        oa_type = _CONVERTER_TYPE_MAP.get(converter, "string")
        param: dict[str, Any] = {
            "in": "path",
            "name": param_name,
            "required": True,
            "schema": {"type": oa_type},
        }
        # UUID format hint
        if converter == "uuid":
            param["schema"]["format"] = "uuid"
        params.append(param)
        return "{" + param_name + "}"

    openapi_path = _PATH_VAR_RE.sub(_replace, rule_str)
    return openapi_path, params
