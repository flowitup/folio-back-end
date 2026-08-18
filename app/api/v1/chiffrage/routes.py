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

from io import BytesIO

from flask import Response, jsonify, request, send_file
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
    ImageFromUrlBody,
    RoomCreateBody,
    RoomUpdateBody,
    StoreCreateBody,
    StoreUpdateBody,
    QuoteCreateBody,
    QuoteUpdateBody,
    ReorderBody,
    UnitCreateBody,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.chiffrage.exceptions import (
    ArticleImageNotFoundError,
    ArticleNotFoundError,
    ChiffragePermissionDeniedError,
    InvalidChiffrageInputError,
    NotProjectMemberError,
    PosteNotFoundError,
    QuoteNotFoundError,
    RoomAlreadyExistsError,
    RoomNotFoundError,
    ImageTooLargeError,
    SsrfBlockedError,
    StoreNotFoundError,
    UnsupportedImageTypeError,
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
    except UnsupportedImageTypeError as e:
        return _err(415, "UnsupportedMediaType", str(e))
    except ImageTooLargeError as e:
        return _err(413, "FileTooLarge", str(e))
    except SsrfBlockedError as e:
        return _err(400, "InvalidInput", str(e))
    except (
        PosteNotFoundError,
        ArticleImageNotFoundError,
        ArticleNotFoundError,
        QuoteNotFoundError,
        RoomNotFoundError,
        StoreNotFoundError,
        UnitNotFoundError,
    ) as e:
        return _err(404, "NotFound", str(e))
    except (UnitAlreadyExistsError, RoomAlreadyExistsError) as e:
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


def _room_json(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "position": r.position}


def _store_json(s: Any) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "poste_id": str(s.poste_id),
        "name": s.name,
        "address": s.address,
        "website_url": s.website_url,
        "position": s.position,
    }


def _article_json(a: Any) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "poste_id": str(a.poste_id),
        "name": a.name,
        "quantity": float(a.quantity),
        "unit": a.unit,
        "note": a.note,
        "room_id": str(a.room_id) if a.room_id else None,
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
# Stores — where to go and buy a poste's items
# ---------------------------------------------------------------------------


@chiffrage_bp.post("/projects/<project_id>/chiffrage/postes/<poste_id>/stores")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_store(project_id: str, poste_id: str) -> Any:
    """Add a shop to visit for this poste."""

    def run() -> Any:
        body = _parse(StoreCreateBody)
        store = get_container().create_chiffrage_store_usecase.execute(
            project_id=UUID(project_id),
            poste_id=UUID(poste_id),
            name=body.name,
            address=body.address,
            website_url=body.website_url,
        )
        return jsonify(_store_json(store)), 201

    return _handle(run)


@chiffrage_bp.patch("/projects/<project_id>/chiffrage/stores/<store_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def update_store(project_id: str, store_id: str) -> Any:
    """Rename a shop, correct its address or its website."""

    def run() -> Any:
        from app.domain.entities.chiffrage_store import ChiffrageStore

        body = _parse(StoreUpdateBody)
        unset = ChiffrageStore._UNSET
        store = get_container().update_chiffrage_store_usecase.execute(
            project_id=UUID(project_id),
            store_id=UUID(store_id),
            name=_sentinel(body, "name", unset),
            address=_sentinel(body, "address", unset),
            website_url=_sentinel(body, "website_url", unset),
        )
        return jsonify(_store_json(store)), 200

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/stores/<store_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_store(project_id: str, store_id: str) -> Any:
    """Remove a shop from a poste."""

    def run() -> Any:
        get_container().delete_chiffrage_store_usecase.execute(project_id=UUID(project_id), store_id=UUID(store_id))
        return "", 204

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
            room_id=body.room_id,
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
            room_id=_sentinel(body, "room_id", unset),
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


# ---------------------------------------------------------------------------
# Article photos
# ---------------------------------------------------------------------------


@chiffrage_bp.get("/projects/<project_id>/chiffrage/articles/<article_id>/image")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:read")
@require_project_access()
@limiter.limit(READ_LIMIT, key_func=jwt_user_key)
def get_article_image(project_id: str, article_id: str) -> Any:
    """Stream an article photo inline.

    Bytes are proxied through the API rather than served from the object store:
    the store endpoint is not browser-reachable, and the app CSP only allows
    images from our own origin. nosniff + a locked-down CSP guard against
    MIME-sniffing user-supplied bytes into something renderable.
    """
    try:
        stream, length, content_type = get_container().get_chiffrage_article_image_usecase.execute(
            project_id=UUID(project_id), article_id=UUID(article_id)
        )
    except (ArticleNotFoundError, ArticleImageNotFoundError):
        return _err(404, "NotFound", "Article or image not found.")
    except Exception:
        logger.exception("get_article_image error article_id=%s", article_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    response = send_file(stream, mimetype=content_type or "application/octet-stream")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Cache-Control"] = "private, max-age=300"
    if length:
        response.headers["Content-Length"] = str(length)
    return response


@chiffrage_bp.post("/projects/<project_id>/chiffrage/articles/<article_id>/image")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def upload_article_image(project_id: str, article_id: str) -> Any:
    """Upload a photo for an article (multipart field 'image')."""

    def run() -> Any:
        if "image" not in request.files:
            return _err(422, "ValidationError", "Multipart field 'image' is required.")
        file = request.files["image"]
        raw = file.stream.read()
        get_container().upload_chiffrage_article_image_usecase.execute(
            project_id=UUID(project_id),
            article_id=UUID(article_id),
            fileobj=BytesIO(raw),
            content_type=file.content_type or "application/octet-stream",
            size=len(raw),
        )
        return jsonify({"ok": True}), 201

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/articles/<article_id>/image-from-url")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit("10 per minute", key_func=jwt_user_key)
def set_article_image_from_url(project_id: str, article_id: str) -> Any:
    """Fetch a supplier image server-side and store it for the article."""

    def run() -> Any:
        body = _parse(ImageFromUrlBody)
        get_container().set_chiffrage_article_image_from_url_usecase.execute(
            project_id=UUID(project_id), article_id=UUID(article_id), url=body.url
        )
        return jsonify({"ok": True}), 201

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/articles/<article_id>/image")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_article_image(project_id: str, article_id: str) -> Any:
    """Detach an article's photo."""

    def run() -> Any:
        get_container().delete_chiffrage_article_image_usecase.execute(
            project_id=UUID(project_id), article_id=UUID(article_id)
        )
        return "", 204

    return _handle(run)


# ---------------------------------------------------------------------------
# Rooms — the chantier's pièces, shared by every poste
# ---------------------------------------------------------------------------


@chiffrage_bp.get("/projects/<project_id>/chiffrage/rooms")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:read")
@require_project_access()
@limiter.limit(READ_LIMIT, key_func=jwt_user_key)
def list_rooms(project_id: str) -> Any:
    """List the project's rooms in display order."""

    def run() -> Any:
        rooms = get_container().list_chiffrage_rooms_usecase.execute(project_id=UUID(project_id))
        return jsonify([_room_json(r) for r in rooms]), 200

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/rooms")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def create_room(project_id: str) -> Any:
    """Declare a room for this chantier."""

    def run() -> Any:
        body = _parse(RoomCreateBody)
        room = get_container().create_chiffrage_room_usecase.execute(project_id=UUID(project_id), name=body.name)
        return jsonify(_room_json(room)), 201

    return _handle(run)


@chiffrage_bp.patch("/projects/<project_id>/chiffrage/rooms/<room_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def update_room(project_id: str, room_id: str) -> Any:
    """Rename a room. Articles hold its id, so they follow the rename."""

    def run() -> Any:
        body = _parse(RoomUpdateBody)
        room = get_container().update_chiffrage_room_usecase.execute(
            project_id=UUID(project_id), room_id=UUID(room_id), name=body.name
        )
        return jsonify(_room_json(room)), 200

    return _handle(run)


@chiffrage_bp.delete("/projects/<project_id>/chiffrage/rooms/<room_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def delete_room(project_id: str, room_id: str) -> Any:
    """Delete a room; its articles resurface as unassigned rather than vanish."""

    def run() -> Any:
        get_container().delete_chiffrage_room_usecase.execute(project_id=UUID(project_id), room_id=UUID(room_id))
        return "", 204

    return _handle(run)


@chiffrage_bp.post("/projects/<project_id>/chiffrage/rooms/<room_id>/reorder")
@jwt_required()  # type: ignore[untyped-decorator]
@require_permission("project:manage_invoices")
@require_project_access(write=True)
@limiter.limit(WRITE_LIMIT, key_func=jwt_user_key)
def reorder_room(project_id: str, room_id: str) -> Any:
    """Move a room between two neighbours."""

    def run() -> Any:
        body = _parse(ReorderBody)
        room = get_container().reorder_chiffrage_room_usecase.execute(
            project_id=UUID(project_id),
            room_id=UUID(room_id),
            before_id=body.before_id,
            after_id=body.after_id,
        )
        return jsonify(_room_json(room)), 200

    return _handle(run)
