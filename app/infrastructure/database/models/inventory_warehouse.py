"""SQLAlchemy ORM model for inventory_warehouses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.warehouse import Warehouse
from app.infrastructure.database.models.base import Base


class InventoryWarehouseModel(Base):
    """A storage place of a company. Deleting the company cascades; rows pointing here RESTRICT."""

    __tablename__ = "inventory_warehouses"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_inventory_warehouses_company_id", "company_id"),)

    def to_entity(self) -> Warehouse:
        return Warehouse(
            id=self.id,
            company_id=self.company_id,
            name=self.name,
            address=self.address,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, w: Warehouse) -> "InventoryWarehouseModel":
        return cls(
            id=w.id,
            company_id=w.company_id,
            name=w.name,
            address=w.address,
            created_at=w.created_at,
            updated_at=w.updated_at,
        )

    def update_from_entity(self, w: Warehouse) -> None:
        self.name = w.name
        self.address = w.address
        self.updated_at = w.updated_at

    def __repr__(self) -> str:
        return f"<InventoryWarehouseModel {self.id} '{self.name}' company={self.company_id}>"
