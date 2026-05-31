"""
Pydantic v2 → apispec component registration helpers.

Converts Pydantic BaseModel subclasses to OpenAPI 3.0 JSON Schema components
and registers them (with all nested $defs) into an apispec APISpec instance.
Deduplication: silently skips re-registration of identical schemas; raises
ValueError on genuine name collisions with differing content.
"""

from __future__ import annotations

import copy
from typing import Any, Type

from apispec import APISpec
from pydantic import BaseModel


def register_model(spec: APISpec, model: Type[BaseModel]) -> str:
    """
    Register a Pydantic v2 model and all its nested sub-schemas into *spec*.

    Steps:
    1. Generate the full JSON schema (with $defs for nested models).
    2. Hoist each $defs entry as a standalone component.
    3. Register the top-level model itself (with $defs stripped).
    4. Deduplicate: skip if the component was already registered with the
       same schema; raise ValueError for true name collisions.

    Returns the $ref string for the model: ``#/components/schemas/<ModelName>``.
    """
    raw_schema: dict[str, Any] = model.model_json_schema(ref_template="#/components/schemas/{model}")

    # Extract and register all nested $defs first so forward refs resolve.
    defs: dict[str, Any] = raw_schema.pop("$defs", {})
    for def_name, def_schema in defs.items():
        _register_component(spec, def_name, def_schema)

    # Register the model itself (without $defs).
    model_name: str = model.__name__
    _register_component(spec, model_name, raw_schema)

    return f"#/components/schemas/{model_name}"


def _register_component(spec: APISpec, name: str, schema: dict[str, Any]) -> None:
    """
    Register a single JSON schema dict as an OpenAPI component.

    Idempotent for identical schemas; raises on true name collision.
    """
    existing = spec.to_dict().get("components", {}).get("schemas", {}).get(name)
    if existing is not None:
        if _schemas_equal(existing, schema):
            return  # already registered — no-op
        raise ValueError(
            f"OpenAPI schema name collision for '{name}': "
            f"attempting to register a different schema under the same name."
        )
    spec.components.schema(name, component=copy.deepcopy(schema))


def _schemas_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Deep equality check between two JSON schema dicts."""
    return a == b
