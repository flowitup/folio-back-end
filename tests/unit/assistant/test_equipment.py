"""Unit tests for `app.application.assistant.equipment` (find/move "où est X ?").

Uses the REAL `SqlAlchemyInventoryItemRepository`/`SqlAlchemyInventoryWarehouseRepository`
against an in-memory SQLite DB (the `session` fixture from `tests/conftest.py`) so the
`q=` ILIKE matching that D6 relies on is exercised for real, not re-implemented as a
fake. Projects and company membership are simple in-memory fakes — no HTTP, no Flask.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.application.assistant.equipment import EquipmentService
from app.application.inventory.exceptions import CompanyAccessDeniedError, InsufficientPermissionError
from app.application.inventory.item_usecases import UpdateInventoryItemUseCase
from app.domain.entities.inventory_item import InventoryItem
from app.domain.entities.project import Project
from app.domain.entities.warehouse import Warehouse
from app.infrastructure.database.repositories.sqlalchemy_inventory_repository import (
    SqlAlchemyInventoryItemRepository,
    SqlAlchemyInventoryWarehouseRepository,
)
from datetime import datetime, timezone


class FakeProjectRepo:
    """Just enough of `IProjectRepository` for equipment.py's name resolution."""

    def __init__(self, projects: list[Project]) -> None:
        self._by_id = {p.id: p for p in projects}

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return self._by_id.get(project_id)

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return [p for p in self._by_id.values() if p.owner_id in company_ids]


class FakeProjectCompanyReader:
    def __init__(self, owners: dict[UUID, UUID]) -> None:
        self._owners = owners

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self._owners.get(project_id)


class FakeEquipmentAuthzReader:
    """Just enough of `AuthzReaderPort` for `_resolve_project`'s `accessible_projects`
    call: the caller is an admin of every company in `admin_of`, and every
    project in `owners` belongs to its mapped company — matches `world`'s single-company
    setup, so `project:read` resolves True for both of its projects."""

    def __init__(self, owners: dict[UUID, UUID], *, admin_of: list[UUID]) -> None:
        self._owners = owners
        self._admin_of = admin_of

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return "admin" if company_id in self._admin_of else None

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self._owners.get(project_id)

    def project_exists(self, project_id: UUID) -> bool:
        return project_id in self._owners

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        return list(self._admin_of)

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> list:
        return []


class FakeMembership:
    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        return True


class FakeChecker:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        return self._allowed

    def has_permission_in_company(self, user_id: UUID, permission_name: str, company_id: UUID) -> bool:
        return self._allowed


def _project(owner_id: UUID, name: str) -> Project:
    return Project(id=uuid4(), name=name, owner_id=owner_id, created_at=datetime.now(timezone.utc))


@pytest.fixture
def world(session):
    """A company with one warehouse, one project, and two inventory rows."""
    company_id = uuid4()
    user_id = uuid4()
    project = _project(company_id, "Villa Arcueil")
    other_project = _project(company_id, "Extension Meaux")

    item_repo = SqlAlchemyInventoryItemRepository(session)
    warehouse_repo = SqlAlchemyInventoryWarehouseRepository(session)
    warehouse = warehouse_repo.add(Warehouse.create(company_id=company_id, name="Entrepôt principal"))

    drill = item_repo.add(
        InventoryItem.create(
            company_id=company_id,
            name="Perceuse Bosch",
            quantity=2,
            condition="working",
            location_type="warehouse",
            warehouse_id=warehouse.id,
        )
    )
    session.commit()

    project_repo = FakeProjectRepo([project, other_project])
    project_reader = FakeProjectCompanyReader({project.id: company_id, other_project.id: company_id})
    authz_reader = FakeEquipmentAuthzReader(
        {project.id: company_id, other_project.id: company_id}, admin_of=[company_id]
    )
    update_item_usecase = UpdateInventoryItemUseCase(
        item_repo=item_repo,
        warehouse_repo=warehouse_repo,
        project_reader=project_reader,
        membership_reader=FakeMembership(),
        permission_checker=FakeChecker(allowed=True),
        db_session=session,
    )
    service = EquipmentService(
        item_repo=item_repo,
        warehouse_repo=warehouse_repo,
        project_repo=project_repo,
        update_item_usecase=update_item_usecase,
        authz_reader=authz_reader,
    )
    return {
        "service": service,
        "company_id": company_id,
        "user_id": user_id,
        "project": project,
        "other_project": other_project,
        "warehouse": warehouse,
        "drill": drill,
        "item_repo": item_repo,
        "warehouse_repo": warehouse_repo,
        "project_reader": project_reader,
        "authz_reader": authz_reader,
        "session": session,
    }


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def test_find_by_exact_name_returns_the_warehouse_location(world) -> None:
    result = world["service"].find(company_ids=[world["company_id"]], query="Perceuse")
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.name == "Perceuse Bosch"
    assert hit.location_label == "Entrepôt principal"
    assert hit.quantity == 2


def test_find_by_alias_in_another_language_matches_the_same_row(world) -> None:
    # "perceuse" is aliased to "máy khoan"/"drill" — searching the Vietnamese alias must
    # still hit the French-named row via aliases.expand().
    result = world["service"].find(company_ids=[world["company_id"]], query="máy khoan")
    assert [hit.name for hit in result.hits] == ["Perceuse Bosch"]


def test_find_no_match_returns_empty_hits(world) -> None:
    result = world["service"].find(company_ids=[world["company_id"]], query="grue mobile")
    assert result.hits == []


def test_find_is_scoped_to_the_given_companies(world) -> None:
    other_company_id = uuid4()
    result = world["service"].find(company_ids=[other_company_id], query="Perceuse")
    assert result.hits == []


def test_find_after_move_reports_the_new_project_location(world) -> None:
    world["service"].move_by_item_id(
        user_id=world["user_id"], item_id=world["drill"].id, project_id=world["project"].id, is_write_confirmed=True
    )
    result = world["service"].find(company_ids=[world["company_id"]], query="Perceuse")
    assert result.hits[0].location_label == "Villa Arcueil"


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


def test_move_not_found(world) -> None:
    outcome = world["service"].move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="grue mobile",
        project_hint="Villa Arcueil",
        is_write_confirmed=True,
    )
    assert outcome.status == "not_found"


def test_move_ambiguous_item_lists_up_to_five_candidates(world) -> None:
    world["item_repo"].add(
        InventoryItem.create(
            company_id=world["company_id"],
            name="Perceuse Makita",
            quantity=1,
            condition="working",
            location_type="warehouse",
            warehouse_id=world["warehouse"].id,
        )
    )
    world["session"].commit()

    outcome = world["service"].move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Villa Arcueil",
        is_write_confirmed=True,
    )
    assert outcome.status == "ambiguous_item"
    assert {hit.name for hit in outcome.candidates} == {"Perceuse Bosch", "Perceuse Makita"}


def test_move_ambiguous_project_when_hint_does_not_resolve(world) -> None:
    outcome = world["service"].move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Chantier inconnu",
        is_write_confirmed=True,
    )
    assert outcome.status == "ambiguous_project"
    assert outcome.item is not None
    assert outcome.item.name == "Perceuse Bosch"
    assert set(outcome.project_candidates) == {"Villa Arcueil", "Extension Meaux"}


def test_move_asks_for_confirmation_when_is_write_not_confirmed(world) -> None:
    outcome = world["service"].move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Villa Arcueil",
        is_write_confirmed=False,
    )
    assert outcome.status == "confirm"
    assert outcome.project_name == "Villa Arcueil"
    # Nothing was actually moved yet.
    unchanged = world["item_repo"].find_by_id(world["drill"].id)
    assert unchanged is not None and unchanged.location_type == "warehouse"


def test_move_executes_the_location_patch_when_confirmed(world) -> None:
    outcome = world["service"].move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Villa Arcueil",
        is_write_confirmed=True,
    )
    assert outcome.status == "moved"
    assert outcome.project_name == "Villa Arcueil"
    moved = world["item_repo"].find_by_id(world["drill"].id)
    assert moved is not None
    assert moved.location_type == "site"
    assert moved.project_id == world["project"].id


def test_move_denied_when_caller_lacks_manage_permission(world) -> None:
    denied_update_usecase = UpdateInventoryItemUseCase(
        item_repo=world["item_repo"],
        warehouse_repo=world["warehouse_repo"],
        project_reader=world["project_reader"],
        membership_reader=FakeMembership(),
        permission_checker=FakeChecker(allowed=False),
        db_session=world["session"],
    )
    service = EquipmentService(
        item_repo=world["item_repo"],
        warehouse_repo=world["warehouse_repo"],
        project_repo=FakeProjectRepo([world["project"], world["other_project"]]),
        update_item_usecase=denied_update_usecase,
        authz_reader=world["authz_reader"],
    )
    outcome = service.move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Villa Arcueil",
        is_write_confirmed=True,
    )
    assert outcome.status == "denied"


def test_move_denied_raises_neither_company_access_nor_permission_error(world) -> None:
    """A permission failure must be a normal `MoveResult`, never an unhandled exception."""
    denied_update_usecase = UpdateInventoryItemUseCase(
        item_repo=world["item_repo"],
        warehouse_repo=world["warehouse_repo"],
        project_reader=world["project_reader"],
        membership_reader=FakeMembership(),
        permission_checker=FakeChecker(allowed=False),
        db_session=world["session"],
    )
    service = EquipmentService(
        item_repo=world["item_repo"],
        warehouse_repo=world["warehouse_repo"],
        project_repo=FakeProjectRepo([world["project"]]),
        update_item_usecase=denied_update_usecase,
        authz_reader=world["authz_reader"],
    )
    try:
        outcome = service.move_by_item_id(
            user_id=world["user_id"],
            item_id=world["drill"].id,
            project_id=world["project"].id,
            is_write_confirmed=True,
        )
    except (CompanyAccessDeniedError, InsufficientPermissionError):  # pragma: no cover - documents the contract
        pytest.fail("EquipmentService must translate permission errors into MoveResult(status='denied')")
    assert outcome.status == "denied"


# ---------------------------------------------------------------------------
# Equipment moves must never enumerate another company's projects.
# ---------------------------------------------------------------------------


def test_move_ambiguous_project_never_offers_a_project_of_another_company(world) -> None:
    """The caller owns/is-assigned-to a project of a DIFFERENT company; the old
    `list_for_user_and_companies(user_id, [item.company_id])` union still returned it
    (it only filters "belongs to this company" for the admin-of branch, never for the
    owner/member branch). `_resolve_project` must keep only projects of the item's own
    company."""
    other_company_id = uuid4()
    foreign_project = _project(other_company_id, "Chantier d'une autre société")
    project_repo = FakeProjectRepo([world["project"], world["other_project"], foreign_project])
    authz_reader = FakeEquipmentAuthzReader(
        {
            world["project"].id: world["company_id"],
            world["other_project"].id: world["company_id"],
            foreign_project.id: other_company_id,
        },
        # The caller administers BOTH companies — exactly the scenario the old union
        # leaked: an admin of company A asking in company A's channel must still never
        # see company B's projects just because they also administer B.
        admin_of=[world["company_id"], other_company_id],
    )
    service = EquipmentService(
        item_repo=world["item_repo"],
        warehouse_repo=world["warehouse_repo"],
        project_repo=project_repo,
        update_item_usecase=world["service"]._update_item,
        authz_reader=authz_reader,
    )
    outcome = service.move(
        user_id=world["user_id"],
        company_ids=[world["company_id"]],
        query="perceuse",
        project_hint="Chantier inconnu",
        is_write_confirmed=True,
    )
    assert outcome.status == "ambiguous_project"
    assert "Chantier d'une autre société" not in outcome.project_candidates
    assert set(outcome.project_candidates) == {"Villa Arcueil", "Extension Meaux"}


# ---------------------------------------------------------------------------
# Search stops after MAX_CANDIDATES + 1 rows.
# ---------------------------------------------------------------------------


def test_find_caps_hits_and_reports_truncated_for_a_large_match_set(world) -> None:
    for i in range(8):
        world["item_repo"].add(
            InventoryItem.create(
                company_id=world["company_id"],
                name=f"Perceuse modèle {i}",
                quantity=1,
                condition="working",
                location_type="warehouse",
                warehouse_id=world["warehouse"].id,
            )
        )
    world["session"].commit()

    result = world["service"].find(company_ids=[world["company_id"]], query="perceuse")
    assert len(result.hits) == 5
    assert result.truncated is True


# ---------------------------------------------------------------------------
# LIKE wildcard escaping — `%`/`_` in a free-text query must not turn into a match-all.
# ---------------------------------------------------------------------------


def test_find_percent_query_does_not_match_every_item(world) -> None:
    result = world["service"].find(company_ids=[world["company_id"]], query="%")
    assert result.hits == []


def test_find_underscore_query_does_not_match_every_item(world) -> None:
    result = world["service"].find(company_ids=[world["company_id"]], query="_")
    assert result.hits == []
