"""
OpenAPI documentation decorator for Flask route handlers.

Attaches metadata to view functions via ``_openapi_meta`` attribute without
altering request/response behavior in any way. The generator reads this
metadata when building the OpenAPI spec.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Type

from pydantic import BaseModel


def openapi_doc(
    *,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    request: Optional[Type[BaseModel]] = None,
    query: Optional[Type[BaseModel]] = None,
    responses: Optional[dict[int, Type[BaseModel]]] = None,
    tags: Optional[list[str]] = None,
    auth: bool = True,
) -> Callable:
    """
    Decorator that annotates a Flask route handler with OpenAPI metadata.

    Does NOT wrap the function — returns the original callable unchanged
    with ``_openapi_meta`` set as a plain attribute. This guarantees zero
    runtime overhead and no interference with Flask's view dispatch.

    Args:
        summary:     Short one-line description shown as the operation summary.
        description: Longer Markdown description for the operation.
        request:     Pydantic model class for the JSON request body.
        query:       Pydantic model class whose fields become query parameters.
        responses:   Mapping of HTTP status code → Pydantic response model.
        tags:        List of tag strings for grouping in the UI.
        auth:        If False, the bearerAuth security requirement is omitted
                     (use for public endpoints like login/refresh).
    """

    def decorator(func: Callable) -> Callable:
        func._openapi_meta: dict[str, Any] = {  # type: ignore[attr-defined]
            "summary": summary,
            "description": description,
            "request": request,
            "query": query,
            "responses": responses,
            "tags": tags,
            "auth": auth,
        }
        return func

    return decorator
