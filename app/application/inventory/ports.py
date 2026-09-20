"""Port interfaces (Protocols) for the inventory application layer."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.inventory_item import InventoryItem
from app.domain.entities.warehouse import Warehouse

# Same membership / permission / session contracts as the product library —
# the inventory is company-scoped the same way.
from app.application.bibliotheque.ports import (  # noqa: F401
    ICompanyMembershipReader as ICompanyMembershipReader,
    ICompanyPermissionChecker as ICompanyPermissionChecker,
    TransactionalSessionPort as TransactionalSessionPort,
)


class IWarehouseRepository(Protocol):
    """Persistence contract for the Warehouse aggregate."""

    def add(self, warehouse: Warehouse) -> Warehouse: ...

    def save(self, warehouse: Warehouse) -> Warehouse:
        """Persist an updated copy of an existing row."""
        ...

    def find_by_id(self, warehouse_id: UUID) -> Optional[Warehouse]: ...

    def list_by_company(self, company_id: UUID) -> list[Warehouse]:
        """All warehouses of a company ordered by name."""
        ...

    def delete(self, warehouse_id: UUID) -> bool:
        """Delete a warehouse row. Returns True when a row was deleted."""
        ...


class IInventoryItemRepository(Protocol):
    """Persistence contract for InventoryItem rows."""

    def add(self, item: InventoryItem) -> InventoryItem: ...

    def save(self, item: InventoryItem) -> InventoryItem: ...

    def find_by_id(self, item_id: UUID) -> Optional[InventoryItem]: ...

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
        """Rows of a company matching every given filter, ordered by name then creation."""
        ...

    def count_in_warehouse(self, warehouse_id: UUID) -> int:
        """Number of rows stored at a warehouse (rows, not units)."""
        ...

    def delete(self, item_id: UUID) -> bool: ...


class IProjectCompanyReader(Protocol):
    """Answers which company a project belongs to, so a row never points at a foreign site."""

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        """The owning company of a project, or None when the project does not exist."""
        ...
