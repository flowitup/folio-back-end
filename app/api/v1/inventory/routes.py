"""Inventory API routes — company-scoped equipment inventory.

Company resolution mirrors the product library: `?company_id=` on reads
(the caller's primary company when absent), `company_id` in the body on
creates. Every use-case checks membership; writes also require
`inventory:manage` in that company.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.inventory import inventory_bp
from app.api.v1.inventory.schemas import (
    CompanyQuerySchema,
    CreateInventoryItemSchema,
    CreateWarehouseSchema,
    InventoryItemsQuerySchema,
    UpdateInventoryItemSchema,
    UpdateWarehouseSchema,
)
from app.application.inventory.dtos import InventoryItemResponse, WarehouseResponse
from app.application.inventory.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidInventoryInputError,
    InventoryItemNotFoundError,
    WarehouseInUseError,
    WarehouseNotFoundError,
)
from app.application.inventory.item_usecases import UNSET as ITEM_UNSET
from app.application.inventory.warehouse_usecases import UNSET as WAREHOUSE_UNSET
from app.domain.value_objects.inventory import INVENTORY_CONDITIONS, INVENTORY_LOCATION_TYPES
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

_NOT_MEMBER = "Not a member of this company."
_NO_PERMISSION = "inventory:manage permission required."


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _resolve_company_id() -> "tuple[UUID | None, tuple[Response, int] | None]":
    """`?company_id=` wins; without it the caller's primary company is used."""
    raw = request.args.get("company_id")
    if raw:
        try:
            return UUID(raw), None
        except ValueError:
            return None, _err(422, "ValidationError", "company_id must be a valid UUID.")
    reader = getattr(get_container(), "authz_reader", None)
    primary = reader.primary_company_id(UUID(get_jwt_identity())) if reader is not None else None
    if primary is None:
        return None, _err(403, "Forbidden", "You are not a member of any company.")
    return primary, None


def _optional_uuid(name: str) -> "tuple[Optional[UUID], tuple[Response, int] | None]":
    raw = request.args.get(name)
    if not raw:
        return None, None
    try:
        return UUID(raw), None
    except ValueError:
        return None, _err(422, "ValidationError", f"{name} must be a valid UUID.")


def _sent(body: Any, fields: tuple[str, ...], unset: object) -> dict[str, Any]:
    """Only the fields the client actually sent (absent = unchanged, explicit null = cleared)."""
    return {f: (getattr(body, f) if f in body.model_fields_set else unset) for f in fields}


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------


@inventory_bp.get("/inventory/warehouses")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="List the company's warehouses", query=CompanyQuerySchema, tags=["inventory"])
def list_warehouses() -> Any:
    company_id, company_error = _resolve_company_id()
    if company_error is not None:
        return company_error
    requester_id = UUID(get_jwt_identity())
    try:
        items = get_container().inventory_list_warehouses_usecase.execute(
            requester_id=requester_id, company_id=company_id
        )
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except Exception:
        logger.exception("list_warehouses error company_id=%s", company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"items": [dataclasses.asdict(w) for w in items]}), 200


@inventory_bp.post("/inventory/warehouses")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Create a warehouse", request=CreateWarehouseSchema, tags=["inventory"])
def create_warehouse() -> Any:
    try:
        body = CreateWarehouseSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))
    requester_id = UUID(get_jwt_identity())
    try:
        warehouse = get_container().inventory_create_warehouse_usecase.execute(
            requester_id=requester_id, company_id=body.company_id, name=body.name, address=body.address
        )
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("create_warehouse error company_id=%s", body.company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(dataclasses.asdict(WarehouseResponse.from_entity(warehouse))), 201


@inventory_bp.patch("/inventory/warehouses/<uuid:warehouse_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("120 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Edit a warehouse", request=UpdateWarehouseSchema, tags=["inventory"])
def update_warehouse(warehouse_id: UUID) -> Any:
    try:
        body = UpdateWarehouseSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))
    requester_id = UUID(get_jwt_identity())
    try:
        warehouse = get_container().inventory_update_warehouse_usecase.execute(
            requester_id=requester_id,
            warehouse_id=warehouse_id,
            **_sent(body, ("name", "address"), WAREHOUSE_UNSET),
        )
    except WarehouseNotFoundError:
        return _err(404, "NotFound", "Warehouse not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("update_warehouse error warehouse_id=%s", warehouse_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(dataclasses.asdict(WarehouseResponse.from_entity(warehouse))), 200


@inventory_bp.delete("/inventory/warehouses/<uuid:warehouse_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Delete an empty warehouse", responses={204: None}, tags=["inventory"])
def delete_warehouse(warehouse_id: UUID) -> Any:
    requester_id = UUID(get_jwt_identity())
    try:
        get_container().inventory_delete_warehouse_usecase.execute(requester_id=requester_id, warehouse_id=warehouse_id)
    except WarehouseNotFoundError:
        return _err(404, "NotFound", "Warehouse not found.")
    except WarehouseInUseError:
        return _err(409, "Conflict", "This warehouse still holds inventory rows; move or remove them first.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("delete_warehouse error warehouse_id=%s", warehouse_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@inventory_bp.get("/inventory/items")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="List the company's equipment", query=InventoryItemsQuerySchema, tags=["inventory"])
def list_items() -> Any:
    company_id, company_error = _resolve_company_id()
    if company_error is not None:
        return company_error
    location_type = request.args.get("location_type") or None
    if location_type is not None and location_type not in INVENTORY_LOCATION_TYPES:
        return _err(422, "ValidationError", "location_type must be 'warehouse' or 'site'.")
    condition = request.args.get("condition") or None
    if condition is not None and condition not in INVENTORY_CONDITIONS:
        return _err(422, "ValidationError", "condition must be 'working' or 'damaged'.")
    warehouse_id, error = _optional_uuid("warehouse_id")
    if error is not None:
        return error
    project_id, error = _optional_uuid("project_id")
    if error is not None:
        return error
    q = request.args.get("q") or None

    requester_id = UUID(get_jwt_identity())
    try:
        items = get_container().inventory_list_items_usecase.execute(
            requester_id=requester_id,
            company_id=company_id,
            location_type=location_type,
            warehouse_id=warehouse_id,
            project_id=project_id,
            condition=condition,
            q=q,
        )
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except Exception:
        logger.exception("list_items error company_id=%s", company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"items": [dataclasses.asdict(i) for i in items], "total": len(items)}), 200


@inventory_bp.post("/inventory/items")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Add equipment to the inventory", request=CreateInventoryItemSchema, tags=["inventory"])
def create_item() -> Any:
    try:
        body = CreateInventoryItemSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))
    requester_id = UUID(get_jwt_identity())
    try:
        item = get_container().inventory_create_item_usecase.execute(
            requester_id=requester_id,
            company_id=body.company_id,
            name=body.name,
            quantity=body.quantity,
            condition=body.condition,
            location_type=body.location_type,
            warehouse_id=body.warehouse_id,
            project_id=body.project_id,
            category=body.category,
            reference=body.reference,
            description=body.description,
        )
    except InvalidInventoryInputError as exc:
        return _err(422, "ValidationError", str(exc))
    except WarehouseNotFoundError:
        return _err(404, "NotFound", "Warehouse not found in this company.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("create_item error company_id=%s", body.company_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(dataclasses.asdict(InventoryItemResponse.from_entity(item))), 201


@inventory_bp.get("/inventory/items/<uuid:item_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("120 per minute", key_func=jwt_user_key)
@openapi_doc(summary="One inventory row", tags=["inventory"])
def get_item(item_id: UUID) -> Any:
    requester_id = UUID(get_jwt_identity())
    try:
        item = get_container().inventory_get_item_usecase.execute(requester_id=requester_id, item_id=item_id)
    except InventoryItemNotFoundError:
        return _err(404, "NotFound", "Inventory item not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except Exception:
        logger.exception("get_item error item_id=%s", item_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(dataclasses.asdict(item)), 200


@inventory_bp.patch("/inventory/items/<uuid:item_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("300 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Edit an inventory row", request=UpdateInventoryItemSchema, tags=["inventory"])
def update_item(item_id: UUID) -> Any:
    try:
        body = UpdateInventoryItemSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", str(exc))
    fields = (
        "name",
        "category",
        "reference",
        "description",
        "quantity",
        "condition",
        "location_type",
        "warehouse_id",
        "project_id",
    )
    requester_id = UUID(get_jwt_identity())
    try:
        item = get_container().inventory_update_item_usecase.execute(
            requester_id=requester_id, item_id=item_id, **_sent(body, fields, ITEM_UNSET)
        )
    except InventoryItemNotFoundError:
        return _err(404, "NotFound", "Inventory item not found.")
    except InvalidInventoryInputError as exc:
        return _err(422, "ValidationError", str(exc))
    except WarehouseNotFoundError:
        return _err(404, "NotFound", "Warehouse not found in this company.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("update_item error item_id=%s", item_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify(dataclasses.asdict(InventoryItemResponse.from_entity(item))), 200


@inventory_bp.delete("/inventory/items/<uuid:item_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
@openapi_doc(summary="Remove an inventory row", responses={204: None}, tags=["inventory"])
def delete_item(item_id: UUID) -> Any:
    requester_id = UUID(get_jwt_identity())
    try:
        get_container().inventory_delete_item_usecase.execute(requester_id=requester_id, item_id=item_id)
    except InventoryItemNotFoundError:
        return _err(404, "NotFound", "Inventory item not found.")
    except CompanyAccessDeniedError:
        return _err(403, "Forbidden", _NOT_MEMBER)
    except InsufficientPermissionError:
        return _err(403, "Forbidden", _NO_PERMISSION)
    except Exception:
        logger.exception("delete_item error item_id=%s", item_id)
        return _err(500, "InternalError", "An unexpected error occurred.")
    return "", 204
