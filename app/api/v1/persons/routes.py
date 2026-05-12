"""Persons API routes (Phase 1b-ii).

Two endpoints:
  GET  /persons?q=<query>&limit=<n>  → typeahead search
  POST /persons                       → create a new Person

Both require a JWT — they consume the authenticated user's identity to
populate ``Person.created_by_user_id`` on creation. No project-scoped
permissions are checked here yet: Person is a global identity, and
visibility scoping by accessible projects is layered in Phase 1d once
the FE typeahead consumes this surface.
"""

from typing import Optional
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import BaseModel, Field, ValidationError

from app.api.v1.persons import persons_bp
from app.application.persons import (
    CreatePersonRequest,
    CreatePersonUseCase,
    SearchPersonsRequest,
    SearchPersonsUseCase,
)
from app.application.persons.create_person import InvalidPersonDataError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Request / response schemas (Pydantic)
# ---------------------------------------------------------------------------


class CreatePersonRequestSchema(BaseModel):
    """POST /persons body."""

    name: str = Field(min_length=1, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=50)


class PersonSummarySchema(BaseModel):
    id: str
    name: str
    phone: Optional[str] = None


class PersonResponseSchema(PersonSummarySchema):
    normalized_name: str
    created_by_user_id: str
    created_at: str


class SearchPersonsResponseSchema(BaseModel):
    persons: list[PersonSummarySchema]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(code: str, message: str, status: int):
    """Standardized error envelope (matches labor/worker_routes style)."""
    return jsonify({"error": code, "message": message}), status


def _validation_error_response(e: ValidationError):
    return jsonify(
        {
            "error": "ValidationError",
            "details": [
                {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                for err in e.errors()
            ],
        }
    ), 400


def _current_user_uuid() -> UUID:
    """Return the JWT identity coerced to UUID. JWT identity is a string."""
    return UUID(str(get_jwt_identity()))


def _get_create_usecase() -> CreatePersonUseCase:
    return get_container().create_person_usecase


def _get_search_usecase() -> SearchPersonsUseCase:
    return get_container().search_persons_usecase


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@persons_bp.route("/persons", methods=["GET"])
@jwt_required()
def search_persons():
    """Typeahead search over Persons.

    Query params:
      q     — search substring (matched against normalized_name or exact phone)
      limit — max rows to return (default 20, capped 100)
    """
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "20"))
    except (TypeError, ValueError):
        return _error("ValidationError", "limit must be an integer", 400)

    result = _get_search_usecase().execute(
        SearchPersonsRequest(query=query, limit=limit)
    )

    return jsonify(
        SearchPersonsResponseSchema(
            persons=[
                PersonSummarySchema(id=p.id, name=p.name, phone=p.phone)
                for p in result.persons
            ],
            total=result.total,
        ).model_dump()
    )


@persons_bp.route("/persons", methods=["POST"])
@jwt_required()
@limiter.limit("20 per minute")
def create_person():
    """Create a new Person scoped to the authenticated caller as creator."""
    try:
        body = CreatePersonRequestSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        created = _get_create_usecase().execute(
            CreatePersonRequest(
                name=body.name,
                phone=body.phone,
                created_by_user_id=_current_user_uuid(),
            )
        )
    except InvalidPersonDataError as e:
        return _error("ValidationError", str(e), 400)

    return jsonify(
        PersonResponseSchema(
            id=created.id,
            name=created.name,
            phone=created.phone,
            normalized_name=created.normalized_name,
            created_by_user_id=created.created_by_user_id,
            created_at=created.created_at,
        ).model_dump()
    ), 201
