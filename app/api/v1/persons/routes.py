"""Persons API routes (Phase 1b-ii).

Two endpoints:
  GET  /persons?q=<query>&limit=<n>  → typeahead search
  POST /persons                       → create a new Person

Both require a JWT — they consume the authenticated user's identity to
populate ``Person.created_by_user_id`` on creation. Person is a global
identity used to link Workers across projects/companies; both the
typeahead search and the merge endpoint expose tenant-wide identifiers,
so they are rate-limited and (for merge) gated behind superadmin.
"""

import logging
from typing import Optional
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from pydantic import BaseModel, Field, ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.persons import persons_bp
from app.application.persons import (
    CreatePersonRequest,
    CreatePersonUseCase,
    InvalidMergeError,
    MergePersonsRequest,
    MergePersonsUseCase,
    PersonNotFoundError,
    SearchPersonsRequest,
    SearchPersonsUseCase,
)
from app.application.persons.create_person import InvalidPersonDataError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

# Typeahead search caps. The min query length prevents bulk enumeration
# of the global Person table by iterating single-character prefixes; the
# max result count keeps any single response bounded.
_PERSONS_SEARCH_MIN_Q = 2
_PERSONS_SEARCH_MAX_LIMIT = 20


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


class MergePersonsRequestSchema(BaseModel):
    """POST /persons/<source_id>/merge body."""

    target_person_id: str = Field(min_length=36, max_length=36)


class MergePersonsResponseSchema(BaseModel):
    target_person_id: str
    workers_reassigned: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(code: str, message: str, status: int):
    """Standardized error envelope (matches labor/worker_routes style)."""
    return jsonify({"error": code, "message": message}), status


def _validation_error_response(e: ValidationError):
    return (
        jsonify(
            {
                "error": "ValidationError",
                "details": [
                    {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in e.errors()
                ],
            }
        ),
        400,
    )


def _current_user_uuid() -> UUID:
    """Return the JWT identity coerced to UUID. JWT identity is a string."""
    return UUID(str(get_jwt_identity()))


def _get_create_usecase() -> CreatePersonUseCase:
    return get_container().create_person_usecase


def _get_search_usecase() -> SearchPersonsUseCase:
    return get_container().search_persons_usecase


def _get_merge_usecase() -> MergePersonsUseCase:
    return get_container().merge_persons_usecase


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@persons_bp.route("/persons", methods=["GET"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def search_persons():
    """Typeahead search over Persons.

    Query params:
      q     — search substring (matched against normalized_name or exact phone).
              Min length 2 to prevent enumeration via single-character prefixes.
      limit — max rows to return (default 20, capped 20)
    """
    query = (request.args.get("q") or "").strip()
    if len(query) < _PERSONS_SEARCH_MIN_Q:
        return _error(
            "ValidationError",
            f"q must be at least {_PERSONS_SEARCH_MIN_Q} characters",
            400,
        )
    try:
        limit = int(request.args.get("limit", str(_PERSONS_SEARCH_MAX_LIMIT)))
    except (TypeError, ValueError):
        return _error("ValidationError", "limit must be an integer", 400)
    # Cap below the use-case's MAX_LIMIT so an authenticated caller cannot
    # widen the response window past the configured ceiling.
    limit = max(1, min(limit, _PERSONS_SEARCH_MAX_LIMIT))

    result = _get_search_usecase().execute(SearchPersonsRequest(query=query, limit=limit))

    return jsonify(
        SearchPersonsResponseSchema(
            persons=[PersonSummarySchema(id=p.id, name=p.name, phone=p.phone) for p in result.persons],
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

    return (
        jsonify(
            PersonResponseSchema(
                id=created.id,
                name=created.name,
                phone=created.phone,
                normalized_name=created.normalized_name,
                created_by_user_id=created.created_by_user_id,
                created_at=created.created_at,
            ).model_dump()
        ),
        201,
    )


@persons_bp.route("/persons/<source_person_id>/merge", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def merge_persons(source_person_id: str):
    """Merge source Person into target Person.

    Reassigns all of source's Worker rows to target, then deletes source.
    Single DB transaction.

    Person is a global identity. Allowing any authenticated caller to
    merge two arbitrary Person rows would let attackers reassign workers
    (and therefore labor history) across tenants. The endpoint is
    therefore restricted to superadmin until the per-tenant admin model
    for Person merge ships.
    """
    claims = get_jwt()
    permissions = set(claims.get("permissions", []))
    if "*:*" not in permissions:
        try:
            uid = get_jwt_identity()
        except Exception:  # pragma: no cover - defensive
            uid = None
        logger.warning("person_merge denied for non-superadmin user_id=%s", uid)
        return _error("Forbidden", "Superadmin required.", 403)

    try:
        body = MergePersonsRequestSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        source_uuid = UUID(source_person_id)
        target_uuid = UUID(body.target_person_id)
    except ValueError:
        return _error("ValidationError", "Invalid UUID", 400)

    try:
        result = _get_merge_usecase().execute(
            MergePersonsRequest(
                source_person_id=source_uuid,
                target_person_id=target_uuid,
            )
        )
    except InvalidMergeError as e:
        return _error("ValidationError", str(e), 400)
    except PersonNotFoundError as e:
        return _error("NotFound", str(e), 404)

    return jsonify(
        MergePersonsResponseSchema(
            target_person_id=result.target_person_id,
            workers_reassigned=result.workers_reassigned,
        ).model_dump()
    )
