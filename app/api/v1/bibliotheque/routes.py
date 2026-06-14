"""Bibliotheque API routes — company-scoped product library.

Company resolution: company_id is taken from the request body (POST /import)
or from the ?company_id= query parameter (GET endpoints). This mirrors the
billing documents pattern where company_id flows through the request payload
rather than a URL segment.

Authorization layers:
  - All endpoints: @jwt_required() + company membership check inside use-case.
  - POST /import and POST /products/<id>/image: also require bibliotheque:manage.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.bibliotheque import bibliotheque_bp
from app.api.v1.bibliotheque.schemas import (
    CreateProductSchema,
    ImageFromUrlSchema,
    ImportRequestSchema,
    UpdateProductSchema,
)
from app.application.bibliotheque.dtos import ImportRecordDTO, LibraryProductResponse
from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    ImageAlreadyExistsError,
    ImageTooLargeError,
    InsufficientPermissionError,
    InvalidProductInputError,
    ProductAlreadyExistsError,
    ProductNotFoundError,
    SsrfBlockedError,
    SupplierNotFoundError,
    UnsupportedImageTypeError,
)
from app.application.bibliotheque.update_product_usecase import UNSET
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _get_company_id() -> UUID | None:
    """Parse ?company_id= from query string; return None on missing/invalid."""
    raw = request.args.get("company_id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# GET /api/v1/bibliotheque/suppliers
# ---------------------------------------------------------------------------


@bibliotheque_bp.get("/bibliotheque/suppliers")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_suppliers() -> Any:
    """List all suppliers for the company. Requires company membership."""
    company_id = _get_company_id()
    if company_id is None:
        return _err(422, "ValidationError", "company_id query parameter is required and must be a valid UUID.")

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        results = c.bibliotheque_list_suppliers_usecase.execute(requester_id=requester_id, company_id=company_id)
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except Exception:
        logger.exception("list_suppliers error company_id=%s", company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"items": [dataclasses.asdict(r) for r in results]}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/bibliotheque/categories
# ---------------------------------------------------------------------------


@bibliotheque_bp.get("/bibliotheque/categories")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_categories() -> Any:
    """List distinct product categories for the company."""
    company_id = _get_company_id()
    if company_id is None:
        return _err(422, "ValidationError", "company_id query parameter is required and must be a valid UUID.")

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        categories = c.bibliotheque_list_categories_usecase.execute(requester_id=requester_id, company_id=company_id)
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except Exception:
        logger.exception("list_categories error company_id=%s", company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"items": categories}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/bibliotheque/products
# ---------------------------------------------------------------------------


@bibliotheque_bp.get("/bibliotheque/products")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_products() -> Any:
    """List products with optional filters: supplier, category, q, page."""
    company_id = _get_company_id()
    if company_id is None:
        return _err(422, "ValidationError", "company_id query parameter is required and must be a valid UUID.")

    supplier_raw = request.args.get("supplier")
    supplier_id: UUID | None = None
    if supplier_raw:
        try:
            supplier_id = UUID(supplier_raw)
        except ValueError:
            return _err(422, "ValidationError", "supplier must be a valid UUID.")

    category = request.args.get("category") or None
    q = request.args.get("q") or None
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        items, total = c.bibliotheque_list_products_usecase.execute(
            requester_id=requester_id,
            company_id=company_id,
            supplier_id=supplier_id,
            category=category,
            q=q,
            page=page,
        )
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except Exception:
        logger.exception("list_products error company_id=%s", company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"items": [dataclasses.asdict(i) for i in items], "total": total, "page": page}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/bibliotheque/products/<id>
# ---------------------------------------------------------------------------


@bibliotheque_bp.get("/bibliotheque/products/<uuid:product_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_product(product_id: UUID) -> Any:
    """Return a single product with its purchase history."""
    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        detail = c.bibliotheque_get_product_usecase.execute(requester_id=requester_id, product_id=product_id)
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except Exception:
        logger.exception("get_product error product_id=%s", product_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return (
        jsonify(
            {
                "product": dataclasses.asdict(detail.product),
                "purchases": [dataclasses.asdict(p) for p in detail.purchases],
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/bibliotheque/products/<id>/image
# ---------------------------------------------------------------------------


@bibliotheque_bp.get("/bibliotheque/products/<uuid:product_id>/image")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("120 per minute", key_func=jwt_user_key)
def get_product_image(product_id: UUID) -> Any:
    """Stream the product image bytes inline.

    Bytes are proxied through the API rather than served via a presigned
    object-store URL — the store endpoint is not browser-reachable. nosniff +
    a locked-down CSP guard against MIME-sniffing user-controlled bytes into a
    renderable type. Mirrors the invoice attachment download route.
    """
    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        stream, length, content_type = c.bibliotheque_get_product_image_usecase.execute(
            requester_id=requester_id, product_id=product_id
        )
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product or image not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except Exception:
        logger.exception("get_product_image error product_id=%s", product_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    response = send_file(stream, mimetype=content_type or "application/octet-stream")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Cache-Control"] = "private, max-age=300"
    if length:
        response.headers["Content-Length"] = str(length)
    return response


# ---------------------------------------------------------------------------
# POST /api/v1/bibliotheque/import
# ---------------------------------------------------------------------------


@bibliotheque_bp.post("/bibliotheque/import")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("10 per minute", key_func=jwt_user_key)
def import_purchases() -> Any:
    """Bulk-import purchase records (idempotent). Requires bibliotheque:manage."""
    try:
        body = ImportRequestSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))

    requester_id = UUID(get_jwt_identity())
    c = get_container()

    records = [
        ImportRecordDTO(
            supplier_reference=r.supplier_reference,
            product_name=r.product_name,
            quantity=r.quantity,
            unit_price=r.unit_price,
            purchased_at=r.purchased_at,
            source_document_ref=r.source_document_ref,
            source_document_type=r.source_document_type,
            line_index=r.line_index,
            size=r.size,
            category=r.category,
            product_url=r.product_url,
            description=r.description,
        )
        for r in body.records
    ]

    try:
        result = c.bibliotheque_import_usecase.execute(
            requester_id=requester_id,
            company_id=body.company_id,
            supplier_name=body.supplier_name,
            supplier_slug=body.supplier_slug,
            supplier_website_url=body.supplier_website_url,
            supplier_product_url_template=body.supplier_product_url_template,
            records=records,
        )
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("import_purchases error company_id=%s", body.company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(dataclasses.asdict(result)), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/bibliotheque/products/<id>
# ---------------------------------------------------------------------------


@bibliotheque_bp.patch("/bibliotheque/products/<uuid:product_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("300 per minute", key_func=jwt_user_key)
def update_product(product_id: UUID) -> Any:
    """Edit an existing product's metadata. Requires bibliotheque:manage.

    Body (all optional): name, category, description, size, product_url. Only
    fields present in the payload are changed; an explicit null clears a field.
    Image bytes are edited via POST /products/<id>/image[-from-url]. Purchase
    rows and aggregates are never modified.
    """
    try:
        body = UpdateProductSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))

    # Only forward fields the client actually sent (distinguish omitted from explicit null).
    kwargs = {
        f: (getattr(body, f) if f in body.model_fields_set else UNSET)
        for f in ("name", "category", "description", "size", "product_url")
    }

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        product = c.bibliotheque_update_product_usecase.execute(
            requester_id=requester_id,
            product_id=product_id,
            **kwargs,
        )
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("update_product error product_id=%s", product_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(dataclasses.asdict(LibraryProductResponse.from_entity(product))), 200


# ---------------------------------------------------------------------------
# POST /api/v1/bibliotheque/products
# ---------------------------------------------------------------------------


@bibliotheque_bp.post("/bibliotheque/products")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def create_product() -> Any:
    """Create a new library product. Requires bibliotheque:manage."""
    try:
        body = CreateProductSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        product = c.bibliotheque_create_product_usecase.execute(
            requester_id=requester_id,
            company_id=body.company_id,
            name=body.name,
            supplier_id=body.supplier_id,
            supplier_name=body.supplier_name,
            supplier_website_url=body.supplier_website_url,
            supplier_reference=body.supplier_reference,
            category=body.category,
            description=body.description,
            size=body.size,
            product_url=body.product_url,
        )
    except InvalidProductInputError as exc:
        return _err(422, "ValidationError", str(exc))
    except SupplierNotFoundError:
        return _err(404, "NotFound", "Supplier not found in this company.")
    except ProductAlreadyExistsError:
        return _err(409, "Conflict", "A product with this supplier reference already exists.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("create_product error company_id=%s", body.company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(dataclasses.asdict(LibraryProductResponse.from_entity(product))), 201


# ---------------------------------------------------------------------------
# DELETE /api/v1/bibliotheque/products/<id>
# ---------------------------------------------------------------------------


@bibliotheque_bp.delete("/bibliotheque/products/<uuid:product_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def delete_product(product_id: UUID) -> Any:
    """Delete a library product and its purchases. Requires bibliotheque:manage."""
    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        c.bibliotheque_delete_product_usecase.execute(
            requester_id=requester_id,
            product_id=product_id,
        )
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("delete_product error product_id=%s", product_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204


# ---------------------------------------------------------------------------
# POST /api/v1/bibliotheque/products/<id>/image
# ---------------------------------------------------------------------------


@bibliotheque_bp.post("/bibliotheque/products/<uuid:product_id>/image")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def upload_product_image(product_id: UUID) -> Any:
    """Upload image bytes for a product (multipart). Requires bibliotheque:manage."""
    if "image" not in request.files:
        return _err(422, "ValidationError", "Multipart field 'image' is required.")

    file = request.files["image"]
    content_type = file.content_type or "application/octet-stream"
    # Read the stream into memory to get the byte length, then pass back as BytesIO.
    # This is safe because IMAGE_MAX_SIZE_BYTES (10 MB) << MAX_CONTENT_LENGTH (151 MB).
    raw = file.stream.read()
    size_bytes = len(raw)
    import io as _io

    fileobj = _io.BytesIO(raw)

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        key = c.bibliotheque_upload_image_usecase.execute(
            requester_id=requester_id,
            product_id=product_id,
            fileobj=fileobj,
            content_type=content_type,
            size_bytes=size_bytes,
        )
    except UnsupportedImageTypeError as exc:
        return _err(415, "UnsupportedMediaType", str(exc))
    except ImageTooLargeError as exc:
        return _err(413, "FileTooLarge", str(exc))
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("upload_product_image error product_id=%s", product_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"image_storage_key": key}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/bibliotheque/products/<id>/image-from-url
# ---------------------------------------------------------------------------


@bibliotheque_bp.post("/bibliotheque/products/<uuid:product_id>/image-from-url")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("600 per minute", key_func=jwt_user_key)
def fetch_product_image_from_url(product_id: UUID) -> Any:
    """Fetch and store a product image from an allowlisted URL server-side.

    Leroy Merlin / Adeo CDN images are hotlink-protected and cannot be fetched
    from the browser.  This endpoint fetches them server-side using a spoofed
    Referer/User-Agent and stores the bytes via BibliothequeImageStorage.

    Body: {"url": "<https://media.adeo.com/...>"}
    Query: ?force=true  — overwrite an existing image (default: skip if present).
    Requires: JWT + company membership + bibliotheque:manage.
    """
    try:
        body = ImageFromUrlSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))

    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    url = str(body.url)

    requester_id = UUID(get_jwt_identity())
    c = get_container()
    try:
        key = c.bibliotheque_fetch_image_from_url_usecase.execute(
            requester_id=requester_id,
            product_id=product_id,
            url=url,
            force=force,
        )
    except SsrfBlockedError as exc:
        return _err(422, "SsrfBlocked", str(exc))
    except UnsupportedImageTypeError as exc:
        return _err(415, "UnsupportedMediaType", str(exc))
    except ImageTooLargeError as exc:
        return _err(413, "FileTooLarge", str(exc))
    except ImageAlreadyExistsError as exc:
        return _err(409, "Conflict", str(exc))
    except ProductNotFoundError:
        return _err(404, "NotFound", "Product not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", "Not a member of this company.")
    except InsufficientPermissionError:
        return _err(403, "Forbidden", "bibliotheque:manage permission required.")
    except Exception:
        logger.exception("fetch_product_image_from_url error product_id=%s url=%s", product_id, url)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"image_storage_key": key}), 200
