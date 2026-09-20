"""Inventory item use-cases: list / get / create / update / delete equipment rows.

A row's place is checked against the company on every write: a warehouse must
belong to the company, and a site must be one of the company's projects. The
entity enforces the shape invariants (enums, non-negative whole quantity, the
id that goes with the location kind).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.inventory.dtos import InventoryItemResponse
from app.application.inventory.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidInventoryInputError,
    InventoryItemNotFoundError,
    WarehouseNotFoundError,
)
from app.application.inventory.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    IInventoryItemRepository,
    IProjectCompanyReader,
    IWarehouseRepository,
    TransactionalSessionPort,
)
from app.domain.entities.inventory_item import InvalidInventoryItemError, InventoryItem

MANAGE_PERMISSION = "inventory:manage"

# Re-export the entity sentinel so routes can express "field omitted".
UNSET = InventoryItem._UNSET


def _require_member(membership: ICompanyMembershipReader, requester_id: UUID, company_id: UUID) -> None:
    if not membership.is_member(requester_id, company_id):
        raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")


def _require_manage(checker: ICompanyPermissionChecker, requester_id: UUID, company_id: UUID) -> None:
    if not checker.has_permission_in_company(requester_id, MANAGE_PERMISSION, company_id):
        raise InsufficientPermissionError(f"User {requester_id} lacks '{MANAGE_PERMISSION}' in company {company_id}.")


class _PlaceChecker:
    """Shared by create and update: the row's place must be inside its company."""

    def __init__(self, warehouse_repo: IWarehouseRepository, project_reader: IProjectCompanyReader) -> None:
        self._warehouses = warehouse_repo
        self._projects = project_reader

    def check(self, item: InventoryItem) -> None:
        if item.location_type == "warehouse":
            assert item.warehouse_id is not None  # entity invariant
            warehouse = self._warehouses.find_by_id(item.warehouse_id)
            if warehouse is None or warehouse.company_id != item.company_id:
                raise WarehouseNotFoundError(f"Warehouse {item.warehouse_id} not found in company {item.company_id}.")
        else:
            assert item.project_id is not None  # entity invariant
            owner = self._projects.project_company_id(item.project_id)
            if owner is None or owner != item.company_id:
                raise InvalidInventoryInputError(
                    f"Project {item.project_id} does not belong to company {item.company_id}."
                )


class ListInventoryItemsUseCase:
    def __init__(self, item_repo: IInventoryItemRepository, membership_reader: ICompanyMembershipReader) -> None:
        self._items = item_repo
        self._membership = membership_reader

    def execute(
        self,
        *,
        requester_id: UUID,
        company_id: UUID,
        location_type: Optional[str] = None,
        warehouse_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        condition: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[InventoryItemResponse]:
        _require_member(self._membership, requester_id, company_id)
        rows = self._items.list(
            company_id,
            location_type=location_type,
            warehouse_id=warehouse_id,
            project_id=project_id,
            condition=condition,
            q=q,
        )
        return [InventoryItemResponse.from_entity(r) for r in rows]


class GetInventoryItemUseCase:
    def __init__(self, item_repo: IInventoryItemRepository, membership_reader: ICompanyMembershipReader) -> None:
        self._items = item_repo
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, item_id: UUID) -> InventoryItemResponse:
        item = self._items.find_by_id(item_id)
        if item is None:
            raise InventoryItemNotFoundError(f"Inventory item {item_id} not found.")
        _require_member(self._membership, requester_id, item.company_id)
        return InventoryItemResponse.from_entity(item)


class CreateInventoryItemUseCase:
    def __init__(
        self,
        item_repo: IInventoryItemRepository,
        warehouse_repo: IWarehouseRepository,
        project_reader: IProjectCompanyReader,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._items = item_repo
        self._place = _PlaceChecker(warehouse_repo, project_reader)
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(
        self,
        *,
        requester_id: UUID,
        company_id: UUID,
        name: str,
        quantity: int,
        condition: str,
        location_type: str,
        warehouse_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        category: Optional[str] = None,
        reference: Optional[str] = None,
        description: Optional[str] = None,
    ) -> InventoryItem:
        _require_member(self._membership, requester_id, company_id)
        _require_manage(self._checker, requester_id, company_id)
        try:
            item = InventoryItem.create(
                company_id=company_id,
                name=name,
                quantity=quantity,
                condition=condition,
                location_type=location_type,
                warehouse_id=warehouse_id,
                project_id=project_id,
                category=category,
                reference=reference,
                description=description,
            )
        except InvalidInventoryItemError as exc:
            raise InvalidInventoryInputError(str(exc)) from exc
        self._place.check(item)
        persisted = self._items.add(item)
        self._db.commit()
        return persisted


class UpdateInventoryItemUseCase:
    def __init__(
        self,
        item_repo: IInventoryItemRepository,
        warehouse_repo: IWarehouseRepository,
        project_reader: IProjectCompanyReader,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._items = item_repo
        self._place = _PlaceChecker(warehouse_repo, project_reader)
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(
        self,
        *,
        requester_id: UUID,
        item_id: UUID,
        name: object = UNSET,
        category: object = UNSET,
        reference: object = UNSET,
        description: object = UNSET,
        quantity: object = UNSET,
        condition: object = UNSET,
        location_type: object = UNSET,
        warehouse_id: object = UNSET,
        project_id: object = UNSET,
    ) -> InventoryItem:
        item = self._items.find_by_id(item_id)
        if item is None:
            raise InventoryItemNotFoundError(f"Inventory item {item_id} not found.")
        _require_member(self._membership, requester_id, item.company_id)
        _require_manage(self._checker, requester_id, item.company_id)
        try:
            updated = item.with_updates(
                name=name,
                category=category,
                reference=reference,
                description=description,
                quantity=quantity,
                condition=condition,
                location_type=location_type,
                warehouse_id=warehouse_id,
                project_id=project_id,
            )
        except InvalidInventoryItemError as exc:
            raise InvalidInventoryInputError(str(exc)) from exc
        if updated is item:
            return item
        if (
            updated.location_type != item.location_type
            or updated.warehouse_id != item.warehouse_id
            or updated.project_id != item.project_id
        ):
            self._place.check(updated)
        persisted = self._items.save(updated)
        self._db.commit()
        return persisted


class DeleteInventoryItemUseCase:
    def __init__(
        self,
        item_repo: IInventoryItemRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._items = item_repo
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(self, *, requester_id: UUID, item_id: UUID) -> None:
        item = self._items.find_by_id(item_id)
        if item is None:
            raise InventoryItemNotFoundError(f"Inventory item {item_id} not found.")
        _require_member(self._membership, requester_id, item.company_id)
        _require_manage(self._checker, requester_id, item.company_id)
        self._items.delete(item_id)
        self._db.commit()
