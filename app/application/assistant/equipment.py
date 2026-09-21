"""Equipment lookup and move ("où est X ?" / "déplace X vers Y") — inventory DB only.

No LLM call (decision D6): the router (S0) already told us the intent is
`find_equipment`/`move_equipment`; matching is a plain ILIKE over `inventory_items`
(name/reference/description — see `IInventoryItemRepository.list(q=...)`) widened with
`aliases.expand()` so "carrelette" and "máy cắt gạch" find the same row. `move_equipment`
performs the location change through the existing `UpdateInventoryItemUseCase` — its
membership/permission checks are the only authorization this module needs, it does not
duplicate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from app.application.assistant import aliases
from app.application.assistant.exceptions import AssistantError
from app.application.inventory.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidInventoryInputError,
    InventoryItemNotFoundError,
    WarehouseNotFoundError,
)
from app.application.inventory.item_usecases import UpdateInventoryItemUseCase
from app.application.inventory.ports import IInventoryItemRepository, IWarehouseRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.inventory_item import InventoryItem

#: `move_equipment` never asks the user to pick from more than this many candidates.
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class EquipmentHit:
    """One matched inventory row, ready to render."""

    item_id: UUID
    company_id: UUID
    name: str
    quantity: int
    condition: str
    location_label: str
    location_type: str
    warehouse_id: Optional[UUID]
    project_id: Optional[UUID]


@dataclass(frozen=True)
class FindResult:
    hits: list[EquipmentHit] = field(default_factory=list)
    #: True when `hits` was truncated to `MAX_CANDIDATES` — the reply appends a "+N more"
    #: line instead of ever exceeding `ChatMessage`'s body length limit (review finding
    #: MEDIUM 12).
    truncated: bool = False
    total: int = 0


@dataclass(frozen=True)
class MoveResult:
    """Outcome of `EquipmentService.move`.

    status:
      - "not_found"        no item matched the query.
      - "ambiguous_item"   more than one item matched; `candidates` holds up to 5.
      - "ambiguous_project" exactly one item, but `project_hint` did not resolve to
        exactly one of the caller's projects; `project_candidates` holds up to 5 names.
      - "confirm"          exactly one item and project resolved, but `is_write`
        confidence was below the gate — ask before writing.
      - "moved"            the location PATCH went through.
      - "denied"           the caller lacks `inventory:manage` in the item's company.
    """

    status: str
    item: Optional[EquipmentHit] = None
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    candidates: list[EquipmentHit] = field(default_factory=list)
    project_candidates: list[str] = field(default_factory=list)


class EquipmentService:
    def __init__(
        self,
        item_repo: IInventoryItemRepository,
        warehouse_repo: IWarehouseRepository,
        project_repo: IProjectRepository,
        update_item_usecase: UpdateInventoryItemUseCase,
    ) -> None:
        self._items = item_repo
        self._warehouses = warehouse_repo
        self._projects = project_repo
        self._update_item = update_item_usecase

    # ------------------------------------------------------------------
    # Search — shared by find and move.
    # ------------------------------------------------------------------

    def _search(self, company_ids: list[UUID], query: str) -> list[EquipmentHit]:
        terms = aliases.expand(query)
        if not terms:
            return []
        seen_item_ids: set[UUID] = set()
        rows: list[InventoryItem] = []
        for company_id in company_ids:
            for term in terms:
                for row in self._items.list(company_id, q=term):
                    if row.id not in seen_item_ids:
                        seen_item_ids.add(row.id)
                        rows.append(row)
        return [self._to_hit(row) for row in rows]

    def _to_hit(self, item: InventoryItem) -> EquipmentHit:
        if item.location_type == "warehouse" and item.warehouse_id is not None:
            warehouse = self._warehouses.find_by_id(item.warehouse_id)
            location_label = warehouse.name if warehouse is not None else "?"
        elif item.project_id is not None:
            project = self._projects.find_by_id(item.project_id)
            location_label = project.name if project is not None else "?"
        else:
            location_label = "?"
        return EquipmentHit(
            item_id=item.id,
            company_id=item.company_id,
            name=item.name,
            quantity=item.quantity,
            condition=item.condition,
            location_label=location_label,
            location_type=item.location_type,
            warehouse_id=item.warehouse_id,
            project_id=item.project_id,
        )

    def find(self, *, company_ids: list[UUID], query: str) -> FindResult:
        hits = self._search(company_ids, query)
        return FindResult(hits=hits[:MAX_CANDIDATES], truncated=len(hits) > MAX_CANDIDATES, total=len(hits))

    # ------------------------------------------------------------------
    # Move
    # ------------------------------------------------------------------

    def _resolve_project(
        self, *, company_id: UUID, user_id: UUID, project_hint: Optional[str]
    ) -> tuple[Optional[UUID], Optional[str], list[str]]:
        """(project_id, project_name, candidate_names) — candidates only set when ambiguous."""
        visible = self._projects.list_for_user_and_companies(user_id, [company_id])
        if not project_hint:
            names = [p.name for p in visible][:MAX_CANDIDATES]
            return None, None, names
        needle = project_hint.strip().lower()
        matches = [p for p in visible if p.name.strip().lower() == needle]
        if not matches:
            matches = [p for p in visible if needle in p.name.strip().lower()]
        if len(matches) == 1:
            return matches[0].id, matches[0].name, []
        names = [p.name for p in matches][:MAX_CANDIDATES] or [p.name for p in visible][:MAX_CANDIDATES]
        return None, None, names

    def move_by_item_id(
        self,
        *,
        user_id: UUID,
        item_id: UUID,
        project_id: UUID,
        is_write_confirmed: bool,
    ) -> MoveResult:
        """Execute (or gate) a move once the item and project are already resolved.

        ``is_write_confirmed`` is True both when S0's `is_write` cleared the gate and
        when the caller tapped a choice option — an explicit tap is by definition an
        explicit write confirmation, it does not need a second Jev confidence check.
        """
        item = self._items.find_by_id(item_id)
        if item is None:
            return MoveResult(status="not_found")
        if not is_write_confirmed:
            project = self._projects.find_by_id(project_id)
            hit = self._to_hit(item)
            return MoveResult(
                status="confirm", item=hit, project_id=project_id, project_name=project.name if project else None
            )
        try:
            self._update_item.execute(
                requester_id=user_id, item_id=item_id, location_type="site", project_id=project_id
            )
        except (CompanyAccessDeniedError, InsufficientPermissionError):
            return MoveResult(status="denied", item=self._to_hit(item))
        except (InventoryItemNotFoundError, WarehouseNotFoundError, InvalidInventoryInputError) as exc:
            raise AssistantError(str(exc)) from exc
        project = self._projects.find_by_id(project_id)
        moved = self._items.find_by_id(item_id)
        hit = self._to_hit(moved) if moved is not None else self._to_hit(item)
        return MoveResult(
            status="moved", item=hit, project_id=project_id, project_name=project.name if project else None
        )

    def move_item(
        self,
        *,
        user_id: UUID,
        item_id: UUID,
        project_hint: Optional[str],
        is_write_confirmed: bool,
    ) -> MoveResult:
        """Resolve the project for an already-identified item, then move (or gate/ask)."""
        item = self._items.find_by_id(item_id)
        if item is None:
            return MoveResult(status="not_found")
        project_id, _project_name, candidates = self._resolve_project(
            company_id=item.company_id, user_id=user_id, project_hint=project_hint
        )
        if project_id is None:
            return MoveResult(status="ambiguous_project", item=self._to_hit(item), project_candidates=candidates)
        return self.move_by_item_id(
            user_id=user_id, item_id=item_id, project_id=project_id, is_write_confirmed=is_write_confirmed
        )

    def move(
        self,
        *,
        user_id: UUID,
        company_ids: list[UUID],
        query: str,
        project_hint: Optional[str],
        is_write_confirmed: bool,
    ) -> MoveResult:
        hits = self._search(company_ids, query)
        if not hits:
            return MoveResult(status="not_found")
        if len(hits) > 1:
            return MoveResult(status="ambiguous_item", candidates=hits[:MAX_CANDIDATES])
        return self.move_item(
            user_id=user_id, item_id=hits[0].item_id, project_hint=project_hint, is_write_confirmed=is_write_confirmed
        )
