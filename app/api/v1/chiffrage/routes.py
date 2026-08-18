"""Chiffrage API routes — project-scoped material provisioning.

Every endpoint lives under /projects/<project_id>/... on purpose. The
``require_permission`` decorator resolves the caller's *effective* permissions
(global role UNION their membership role on that project) from the project_id
in the URL, so a user invited as a project manager can edit that project's
chiffrage even when their global role is read-only. Addressing nested entities
without the project in the path would silently fall back to global-only
permissions.

Two decorators guard every route, mirroring the invoices blueprint:
``require_permission`` checks the caller holds the right permission, and
``require_project_access`` checks they may touch *this* project (owner, member,
or admin). Using one source for both reads and writes avoids the incoherent
state where a caller can write to a chiffrage they cannot read.

Ownership of nested entities is re-checked inside the use-cases: a
poste/article/quote id that belongs to a different project is reported as 404,
never acted upon.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import jwt_required
from pydantic import BaseModel, ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.validation_error import safe_validation_fields
from app.api.v1.chiffrage import chiffrage_bp
from app.api.v1.chiffrage.schemas import (
    ArticleCreateBody,
    ArticleUpdateBody,
    PosteCreateBody,
    PosteUpdateBody,
    QuoteCreateBody,
    QuoteUpdateBody,
    ReorderBody,
    UnitCreateBody,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.chiffrage.exceptions import (
    ArticleNotFoundError,
    ChiffragePermissionDeniedError,
    InvalidChiffrageInputError,
    NotProjectMemberError,
    PosteNotFoundError,
    QuoteNotFoundError,
    UnitAlreadyExistsError,
    UnitNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

READ_LIMIT = "60 per minute"
WRITE_LIMIT = "30 per minute"


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _parse(model: type[BaseModel]) -> Any:
    """Validate the JSON body, raising ValidationError for the caller to map."""
    return model.model_validate(request.get_json(silent=True) or {})


def _sentinel(body: BaseModel, field: str, unset: object) -> object:
    """Return the submitted value, or *unset* when the client omitted the field.

    Distinguishing "omitted" from "explicitly null" is what allows a PATCH to
    clear a note without also wiping every other field.
    """
    return getattr(body, field) if field in body.model_fields_set else unset


def _handle(fn: Callable[[], Any]) -> Any:
    """Run a route body, mapping application exceptions to HTTP status codes."""
    try:
        return fn()
    except ValidationError as e:
        return jsonify({"error": "ValidationError", "fields": safe_validation_fields(e)}), 422
    except (PosteNotFoundError, ArticleNotFoundError, QuoteNotFoundError, UnitNotFoundError) as e:
        return _err(404, "NotFound", str(e))
    except UnitAlreadyExistsError as e:
        return _err(409, "Conflict", str(e))
    except InvalidChiffrageInputError as e:
        return _err(400, "InvalidInput", str(e))
    except NotProjectMemberError as e:
        return _err(403, "Forbidden", str(e))
    except ChiffragePermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))


def _serialize(obj: Any) -> Any:
    """Serialize a dataclass DTO tree to JSON-safe primitives."""
    return dataclasses.asdict(obj)


def _poste_json(p: Any) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "project_id": str(p.project_id),
        "name": p.name,
        "note": p.note,
        "position": p.position,
    }


def _article_json(a: Any) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "poste_id": str(a.poste_id),
        "name": a.name,
        "quantity": float(a.quantity),
        "unit": a.unit,
        "note": a.note,
        "position": a.position,
    }


def _quote_json(q: Any) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "article_id": str(q.article_id),
        "supplier_id": str(q.supplier_id) if q.supplier_id else None,
        "supplier_name": q.supplier_name,
        "library_product_id": str(q.library_product_id) if q.library_product_id else None,
        "unit_price_ht": float(q.unit_price_ht),
        "tva_rate": float(q.tva_rate),
        "product_url": q.product_url,
        "note": q.note,
        "is_selected": q.is_selected,
    }


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------


@chiffrage_bp.get("/projects/<project_id>/chiffrage")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit(READ_LIMIT, key_func=jwt_user_key)
def get_chiffrage(project_id: str) -> Any:
    """Return the project's postes, articles, quotes and computed totals."""

    def run() -> Any:
        tree = get_container().get_chiffrage_tree_usecase.execute(project_id=UUID(project_id))
        return jsonify(_serialize(tree)), 200

    return _handle(run)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@chiffrage_bp.get("/projects/<project_id>/chiffrage/units")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit(READ_LIMIT, key_func=jwt_user_key)
def list_units(project_id: str) -> Any:
    """Return preset units plus the project's custom ones."""

    def run() -> Any:
        units = get_container().list_chiffrage_units_usecase.execute(project_id=UUID(project_id))
        return jsonify([dataclasses.asdict(u) for u in units]), 200

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/units")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_unit(project_id: str) -> Any:
    """Add a custom unit symbol to the project."""

    def run() -> Any:
        body = _parse(UnitCreateBody)
        unit = get_container().create_chiffrage_unit_usecase.execute(project_id=UUID(project_id), symbol=body.symbol)
        return jsonify({"id": str(unit.id), "symbol": unit.symbol, "is_preset": False}), 201

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/units/<unit_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_unit(project_id: str, unit_id: str) -> Any:
    """Remove a custom unit. Articles keep their snapshot symbol."""

    def run() -> Any:
        get_container().delete_chiffrage_unit_usecase.execute(project_id=UUID(project_id), unit_id=UUID(unit_id))
        return "", 204

    return _handle(run)


# ---------------------------------------------------------------------------
# Postes
# ---------------------------------------------------------------------------


@chiffrage_bp.post("/projects/<project_id>/chiffrage/postes")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_poste(project_id: str) -> Any:
    """Create a costing section."""

    def run() -> Any:
        body = _parse(PosteCreateBody)
        poste = get_container().create_chiffrage_poste_usecase.execute(
            project_id=UUID(project_id), name=body.name, note=body.note
        )
        return jsonify(_poste_json(poste)), 201

    return _handle(run)


@chiffrage_bp.patch("/projects/<project_id>/chiffrage/postes/<poste_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def update_poste(project_id: str, poste_id: str) -> Any:
    """Rename a poste or edit its note."""

    def run() -> Any:
        from app.domain.entities.chiffrage_poste import ChiffragePoste

        body = _parse(PosteUpdateBody)
        unset = ChiffragePoste._UNSET
        poste = get_container().update_chiffrage_poste_usecase.execute(
            project_id=UUID(project_id),
            poste_id=UUID(poste_id),
            name=_sentinel(body, "name", unset),
            note=_sentinel(body, "note", unset),
        )
        return jsonify(_poste_json(poste)), 200

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/postes/<poste_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_poste(project_id: str, poste_id: str) -> Any:
    """Delete a poste with its articles and quotes."""

    def run() -> Any:
        get_container().delete_chiffrage_poste_usecase.execute(project_id=UUID(project_id), poste_id=UUID(poste_id))
        return "", 204

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/postes/<poste_id>/reorder")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def reorder_poste(project_id: str, poste_id: str) -> Any:
    """Move a poste between two neighbours."""

    def run() -> Any:
        body = _parse(ReorderBody)
        poste = get_container().reorder_chiffrage_poste_usecase.execute(
            project_id=UUID(project_id),
            poste_id=UUID(poste_id),
            before_id=body.before_id,
            after_id=body.after_id,
        )
        return jsonify(_poste_json(poste)), 200

    return _handle(run)


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


@chiffrage_bp.post("/projects/<project_id>/chiffrage/postes/<poste_id>/articles")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_article(project_id: str, poste_id: str) -> Any:
    """Add an article to a poste."""

    def run() -> Any:
        body = _parse(ArticleCreateBody)
        article = get_container().create_chiffrage_article_usecase.execute(
            project_id=UUID(project_id),
            poste_id=UUID(poste_id),
            name=body.name,
            quantity=body.quantity,
            unit=body.unit,
            note=body.note,
        )
        return jsonify(_article_json(article)), 201

    return _handle(run)


@chiffrage_bp.patch("/projects/<project_id>/chiffrage/articles/<article_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def update_article(project_id: str, article_id: str) -> Any:
    """Edit an article's name, quantity, unit or note."""

    def run() -> Any:
        from app.domain.entities.chiffrage_article import ChiffrageArticle

        body = _parse(ArticleUpdateBody)
        unset = ChiffrageArticle._UNSET
        article = get_container().update_chiffrage_article_usecase.execute(
            project_id=UUID(project_id),
            article_id=UUID(article_id),
            name=_sentinel(body, "name", unset),
            quantity=_sentinel(body, "quantity", unset),
            unit=_sentinel(body, "unit", unset),
            note=_sentinel(body, "note", unset),
        )
        return jsonify(_article_json(article)), 200

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/articles/<article_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_article(project_id: str, article_id: str) -> Any:
    """Delete an article with its quotes."""

    def run() -> Any:
        get_container().delete_chiffrage_article_usecase.execute(
            project_id=UUID(project_id), article_id=UUID(article_id)
        )
        return "", 204

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/articles/<article_id>/reorder")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def reorder_article(project_id: str, article_id: str) -> Any:
    """Move an article within its poste."""

    def run() -> Any:
        body = _parse(ReorderBody)
        article = get_container().reorder_chiffrage_article_usecase.execute(
            project_id=UUID(project_id),
            article_id=UUID(article_id),
            before_id=body.before_id,
            after_id=body.after_id,
        )
        return jsonify(_article_json(article)), 200

    return _handle(run)


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


@chiffrage_bp.post("/projects/<project_id>/chiffrage/articles/<article_id>/quotes")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_quote(project_id: str, article_id: str) -> Any:
    """Record a fournisseur price for an article."""

    def run() -> Any:
        body = _parse(QuoteCreateBody)
        quote = get_container().create_chiffrage_quote_usecase.execute(
            project_id=UUID(project_id),
            article_id=UUID(article_id),
            unit_price_ht=body.unit_price_ht,
            tva_rate=body.tva_rate,
            supplier_id=body.supplier_id,
            supplier_name=body.supplier_name,
            library_product_id=body.library_product_id,
            product_url=body.product_url,
            note=body.note,
        )
        return jsonify(_quote_json(quote)), 201

    return _handle(run)


@chiffrage_bp.patch("/projects/<project_id>/chiffrage/quotes/<quote_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def update_quote(project_id: str, quote_id: str) -> Any:
    """Edit a quote's fournisseur, price, VAT rate, link or note."""

    def run() -> Any:
        from app.domain.entities.chiffrage_quote import ChiffrageQuote

        body = _parse(QuoteUpdateBody)
        unset = ChiffrageQuote._UNSET
        quote = get_container().update_chiffrage_quote_usecase.execute(
            project_id=UUID(project_id),
            quote_id=UUID(quote_id),
            supplier_id=_sentinel(body, "supplier_id", unset),
            supplier_name=_sentinel(body, "supplier_name", unset),
            library_product_id=_sentinel(body, "library_product_id", unset),
            unit_price_ht=_sentinel(body, "unit_price_ht", unset),
            tva_rate=_sentinel(body, "tva_rate", unset),
            product_url=_sentinel(body, "product_url", unset),
            note=_sentinel(body, "note", unset),
        )
        return jsonify(_quote_json(quote)), 200

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/quotes/<quote_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_quote(project_id: str, quote_id: str) -> Any:
    """Delete a quote; the article falls back to the cheapest remaining one."""

    def run() -> Any:
        get_container().delete_chiffrage_quote_usecase.execute(project_id=UUID(project_id), quote_id=UUID(quote_id))
        return "", 204

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/quotes/<quote_id>/select")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def select_quote(project_id: str, quote_id: str) -> Any:
    """Mark a quote as the retained offer for its article."""

    def run() -> Any:
        quote = get_container().select_chiffrage_quote_usecase.execute(
            project_id=UUID(project_id), quote_id=UUID(quote_id)
        )
        return jsonify(_quote_json(quote)), 200

    return _handle(run)
