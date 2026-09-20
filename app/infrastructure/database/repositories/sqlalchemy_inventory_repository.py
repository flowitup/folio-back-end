"""SQLAlchemy adapters implementing the inventory repository ports."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.entities.inventory_item import InventoryItem
from app.domain.entities.warehouse import Warehouse
from app.infrastructure.database.models.inventory_item import InventoryItemModel
from app.infrastructure.database.models.inventory_warehouse import InventoryWarehouseModel


class SqlAlchemyInventoryWarehouseRepository:
    """Implements IWarehouseRepository against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, warehouse: Warehouse) -> Warehouse:
        row = InventoryWarehouseModel.from_entity(warehouse)
        self._session.add(row)
        self._session.flush()
        return row.to_entity()

    def save(self, warehouse: Warehouse) -> Warehouse:
        row = self._session.get(InventoryWarehouseModel, warehouse.id)
        if row is None:
            return self.add(warehouse)
        row.update_from_entity(warehouse)
        self._session.flush()
        return row.to_entity()

    def find_by_id(self, warehouse_id: UUID) -> Optional[Warehouse]:
        row = self._session.get(InventoryWarehouseModel, warehouse_id)
        return row.to_entity() if row is not None else None

    def list_by_company(self, company_id: UUID) -> list[Warehouse]:
        rows = (
            self._session.execute(
                select(InventoryWarehouseModel)
                .where(InventoryWarehouseModel.company_id == company_id)
                .order_by(InventoryWarehouseModel.name, InventoryWarehouseModel.created_at)
            )
            .scalars()
            .all()
        )
        return [r.to_entity() for r in rows]

    def delete(self, warehouse_id: UUID) -> bool:
        row = self._session.get(InventoryWarehouseModel, warehouse_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class SqlAlchemyInventoryItemRepository:
    """Implements IInventoryItemRepository against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, item: InventoryItem) -> InventoryItem:
        row = InventoryItemModel.from_entity(item)
        self._session.add(row)
        self._session.flush()
        return row.to_entity()

    def save(self, item: InventoryItem) -> InventoryItem:
        row = self._session.get(InventoryItemModel, item.id)
        if row is None:
            return self.add(item)
        row.update_from_entity(item)
        self._session.flush()
        return row.to_entity()

    def find_by_id(self, item_id: UUID) -> Optional[InventoryItem]:
        row = self._session.get(InventoryItemModel, item_id)
        return row.to_entity() if row is not None else None

    def list(
        self,
        company_id: UUID,
        *,
        location_type: Optional[str] = None,
        warehouse_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        condition: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[InventoryItem]:
        stmt = select(InventoryItemModel).where(InventoryItemModel.company_id == company_id)
        if location_type:
            stmt = stmt.where(InventoryItemModel.location_type == location_type)
        if warehouse_id is not None:
            stmt = stmt.where(InventoryItemModel.warehouse_id == warehouse_id)
        if project_id is not None:
            stmt = stmt.where(InventoryItemModel.project_id == project_id)
        if condition:
            stmt = stmt.where(InventoryItemModel.condition == condition)
        if q:
            needle = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(InventoryItemModel.name).like(needle),
                    func.lower(func.coalesce(InventoryItemModel.reference, "")).like(needle),
                    func.lower(func.coalesce(InventoryItemModel.description, "")).like(needle),
                )
            )
        stmt = stmt.order_by(InventoryItemModel.name, InventoryItemModel.created_at)
        rows = self._session.execute(stmt).scalars().all()
        return [r.to_entity() for r in rows]

    def count_in_warehouse(self, warehouse_id: UUID) -> int:
        total = self._session.execute(
            select(func.count()).select_from(InventoryItemModel).where(InventoryItemModel.warehouse_id == warehouse_id)
        ).scalar_one()
        return int(total)

    def delete(self, item_id: UUID) -> bool:
        row = self._session.get(InventoryItemModel, item_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True
