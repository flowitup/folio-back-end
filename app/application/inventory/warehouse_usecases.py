"""Warehouse use-cases: list / create / update / delete a company's storage places.

Auth order on every write: membership in the company → `inventory:manage` in
that same company. Reads need membership only, like the product library.
"""

from __future__ import annotations

from uuid import UUID

from app.application.inventory.dtos import WarehouseResponse
from app.application.inventory.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    WarehouseInUseError,
    WarehouseNotFoundError,
)
from app.application.inventory.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    IInventoryItemRepository,
    IWarehouseRepository,
    TransactionalSessionPort,
)
from app.domain.entities.warehouse import Warehouse

MANAGE_PERMISSION = "inventory:manage"

# Re-export the entity sentinel so routes can express "field omitted".
UNSET = Warehouse._UNSET


def _require_member(membership: ICompanyMembershipReader, requester_id: UUID, company_id: UUID) -> None:
    if not membership.is_member(requester_id, company_id):
        raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")


def _require_manage(checker: ICompanyPermissionChecker, requester_id: UUID, company_id: UUID) -> None:
    if not checker.has_permission_in_company(requester_id, MANAGE_PERMISSION, company_id):
        raise InsufficientPermissionError(f"User {requester_id} lacks '{MANAGE_PERMISSION}' in company {company_id}.")


class ListWarehousesUseCase:
    def __init__(self, warehouse_repo: IWarehouseRepository, membership_reader: ICompanyMembershipReader) -> None:
        self._warehouses = warehouse_repo
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, company_id: UUID) -> list[WarehouseResponse]:
        _require_member(self._membership, requester_id, company_id)
        return [WarehouseResponse.from_entity(w) for w in self._warehouses.list_by_company(company_id)]


class CreateWarehouseUseCase:
    def __init__(
        self,
        warehouse_repo: IWarehouseRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._warehouses = warehouse_repo
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(self, *, requester_id: UUID, company_id: UUID, name: str, address: str | None = None) -> Warehouse:
        _require_member(self._membership, requester_id, company_id)
        _require_manage(self._checker, requester_id, company_id)
        persisted = self._warehouses.add(Warehouse.create(company_id=company_id, name=name, address=address))
        self._db.commit()
        return persisted


class UpdateWarehouseUseCase:
    def __init__(
        self,
        warehouse_repo: IWarehouseRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._warehouses = warehouse_repo
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(
        self, *, requester_id: UUID, warehouse_id: UUID, name: object = UNSET, address: object = UNSET
    ) -> Warehouse:
        warehouse = self._warehouses.find_by_id(warehouse_id)
        if warehouse is None:
            raise WarehouseNotFoundError(f"Warehouse {warehouse_id} not found.")
        _require_member(self._membership, requester_id, warehouse.company_id)
        _require_manage(self._checker, requester_id, warehouse.company_id)
        updated = warehouse.with_updates(name=name, address=address)
        if updated is warehouse:
            return warehouse
        persisted = self._warehouses.save(updated)
        self._db.commit()
        return persisted


class DeleteWarehouseUseCase:
    """A warehouse that still holds rows is never deleted: the crew moves or removes them first."""

    def __init__(
        self,
        warehouse_repo: IWarehouseRepository,
        item_repo: IInventoryItemRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._warehouses = warehouse_repo
        self._items = item_repo
        self._membership = membership_reader
        self._checker = permission_checker
        self._db = db_session

    def execute(self, *, requester_id: UUID, warehouse_id: UUID) -> None:
        warehouse = self._warehouses.find_by_id(warehouse_id)
        if warehouse is None:
            raise WarehouseNotFoundError(f"Warehouse {warehouse_id} not found.")
        _require_member(self._membership, requester_id, warehouse.company_id)
        _require_manage(self._checker, requester_id, warehouse.company_id)
        held = self._items.count_in_warehouse(warehouse_id)
        if held > 0:
            raise WarehouseInUseError(f"Warehouse {warehouse_id} still holds {held} inventory row(s).")
        self._warehouses.delete(warehouse_id)
        self._db.commit()
