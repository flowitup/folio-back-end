"""SQLAlchemy ORM model for inventory_items."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.inventory_item import InventoryItem
from app.infrastructure.database.models.base import Base


class InventoryItemModel(Base):
    """One batch of identical equipment in one place and one condition.

    `warehouse_id` RESTRICTs so a warehouse in use cannot vanish under its rows
    (the use-case answers 409 first). `project_id` SET NULLs when a project is
    deleted: the row survives as "unknown location" rather than blocking the
    project's deletion, and the clients already render that state.
    """

    __tablename__ = "inventory_items"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    condition: Mapped[str] = mapped_column(String(20), nullable=False)
    location_type: Mapped[str] = mapped_column(String(20), nullable=False)
    warehouse_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_warehouses.id", ondelete="RESTRICT"), nullable=True
    )
    project_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_inventory_items_company_id", "company_id"),
        Index("ix_inventory_items_warehouse_id", "warehouse_id"),
        Index("ix_inventory_items_project_id", "project_id"),
    )

    def to_entity(self) -> InventoryItem:
        return InventoryItem(
            id=self.id,
            company_id=self.company_id,
            name=self.name,
            category=self.category,
            reference=self.reference,
            description=self.description,
            quantity=self.quantity,
            condition=self.condition,
            location_type=self.location_type,
            warehouse_id=self.warehouse_id,
            project_id=self.project_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, i: InventoryItem) -> "InventoryItemModel":
        return cls(
            id=i.id,
            company_id=i.company_id,
            name=i.name,
            category=i.category,
            reference=i.reference,
            description=i.description,
            quantity=i.quantity,
            condition=i.condition,
            location_type=i.location_type,
            warehouse_id=i.warehouse_id,
            project_id=i.project_id,
            created_at=i.created_at,
            updated_at=i.updated_at,
        )

    def update_from_entity(self, i: InventoryItem) -> None:
        self.name = i.name
        self.category = i.category
        self.reference = i.reference
        self.description = i.description
        self.quantity = i.quantity
        self.condition = i.condition
        self.location_type = i.location_type
        self.warehouse_id = i.warehouse_id
        self.project_id = i.project_id
        self.updated_at = i.updated_at

    def __repr__(self) -> str:
        return f"<InventoryItemModel {self.id} '{self.name}' x{self.quantity} {self.condition}>"
