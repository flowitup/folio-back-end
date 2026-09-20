"""Unit tests for the inventory use-cases and entities — in-memory fakes, no Flask, no DB."""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.application.inventory.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidInventoryInputError,
    InventoryItemNotFoundError,
    WarehouseInUseError,
    WarehouseNotFoundError,
)
from app.application.inventory.item_usecases import (
    UNSET,
    CreateInventoryItemUseCase,
    DeleteInventoryItemUseCase,
    GetInventoryItemUseCase,
    ListInventoryItemsUseCase,
    UpdateInventoryItemUseCase,
)
from app.application.inventory.warehouse_usecases import (
    CreateWarehouseUseCase,
    DeleteWarehouseUseCase,
    ListWarehousesUseCase,
    UpdateWarehouseUseCase,
)
from app.domain.entities.inventory_item import InvalidInventoryItemError, InventoryItem
from app.domain.entities.warehouse import Warehouse

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeWarehouseRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, Warehouse] = {}

    def add(self, warehouse: Warehouse) -> Warehouse:
        self.rows[warehouse.id] = warehouse
        return warehouse

    def save(self, warehouse: Warehouse) -> Warehouse:
        self.rows[warehouse.id] = warehouse
        return warehouse

    def find_by_id(self, warehouse_id: UUID) -> Optional[Warehouse]:
        return self.rows.get(warehouse_id)

    def list_by_company(self, company_id: UUID) -> list[Warehouse]:
        return sorted((w for w in self.rows.values() if w.company_id == company_id), key=lambda w: w.name)

    def delete(self, warehouse_id: UUID) -> bool:
        return self.rows.pop(warehouse_id, None) is not None


class FakeItemRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, InventoryItem] = {}

    def add(self, item: InventoryItem) -> InventoryItem:
        self.rows[item.id] = item
        return item

    def save(self, item: InventoryItem) -> InventoryItem:
        self.rows[item.id] = item
        return item

    def find_by_id(self, item_id: UUID) -> Optional[InventoryItem]:
        return self.rows.get(item_id)

    def list(self, company_id, *, location_type=None, warehouse_id=None, project_id=None, condition=None, q=None):
        out = [i for i in self.rows.values() if i.company_id == company_id]
        if location_type:
            out = [i for i in out if i.location_type == location_type]
        if warehouse_id:
            out = [i for i in out if i.warehouse_id == warehouse_id]
        if project_id:
            out = [i for i in out if i.project_id == project_id]
        if condition:
            out = [i for i in out if i.condition == condition]
        if q:
            out = [i for i in out if q.lower() in f"{i.name} {i.reference or ''}".lower()]
        return sorted(out, key=lambda i: i.name)

    def count_in_warehouse(self, warehouse_id: UUID) -> int:
        return sum(1 for i in self.rows.values() if i.warehouse_id == warehouse_id)

    def delete(self, item_id: UUID) -> bool:
        return self.rows.pop(item_id, None) is not None


class FakeProjectReader:
    def __init__(self, owners: dict[UUID, UUID]) -> None:
        self.owners = owners

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self.owners.get(project_id)


class FakeMembership:
    def __init__(self, members: dict[UUID, set[UUID]]) -> None:
        self.members = members  # user → companies

    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        return company_id in self.members.get(user_id, set())


class FakeChecker:
    def __init__(self, managers: dict[UUID, set[UUID]]) -> None:
        self.managers = managers  # user → companies where inventory:manage holds

    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        return bool(self.managers.get(user_id))

    def has_permission_in_company(self, user_id: UUID, permission_name: str, company_id: UUID) -> bool:
        return permission_name == "inventory:manage" and company_id in self.managers.get(user_id, set())


class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def begin_nested(self) -> _Nested:
        return _Nested()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def world():
    company_a, company_b = uuid4(), uuid4()
    manager, member, outsider = uuid4(), uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    warehouses = FakeWarehouseRepo()
    items = FakeItemRepo()
    wh_a = warehouses.add(Warehouse.create(company_id=company_a, name="Kho Bình Thạnh", address="12 NHC"))
    wh_b = warehouses.add(Warehouse.create(company_id=company_b, name="Other co"))
    membership = FakeMembership({manager: {company_a}, member: {company_a}})
    checker = FakeChecker({manager: {company_a}})
    projects = FakeProjectReader({project_a: company_a, project_b: company_b})
    session = FakeSession()
    return dict(
        company_a=company_a,
        company_b=company_b,
        manager=manager,
        member=member,
        outsider=outsider,
        project_a=project_a,
        project_b=project_b,
        warehouses=warehouses,
        items=items,
        wh_a=wh_a,
        wh_b=wh_b,
        membership=membership,
        checker=checker,
        projects=projects,
        session=session,
    )


def _create_item_uc(w):
    return CreateInventoryItemUseCase(
        item_repo=w["items"],
        warehouse_repo=w["warehouses"],
        project_reader=w["projects"],
        membership_reader=w["membership"],
        permission_checker=w["checker"],
        db_session=w["session"],
    )


def _update_item_uc(w):
    return UpdateInventoryItemUseCase(
        item_repo=w["items"],
        warehouse_repo=w["warehouses"],
        project_reader=w["projects"],
        membership_reader=w["membership"],
        permission_checker=w["checker"],
        db_session=w["session"],
    )


def _drill(w, **overrides):
    kwargs = dict(
        requester_id=w["manager"],
        company_id=w["company_a"],
        name="Máy khoan Bosch",
        quantity=3,
        condition="working",
        location_type="warehouse",
        warehouse_id=w["wh_a"].id,
        category="power_tool",
        reference="SN-778",
    )
    kwargs.update(overrides)
    return _create_item_uc(w).execute(**kwargs)


# ---------------------------------------------------------------------------
# Entity invariants
# ---------------------------------------------------------------------------


class TestInventoryItemEntity:
    def test_rejects_negative_or_non_integer_quantity(self):
        for bad in (-1, 1.5, True):
            with pytest.raises(InvalidInventoryItemError):
                InventoryItem.create(
                    company_id=uuid4(),
                    name="x",
                    quantity=bad,  # type: ignore[arg-type]
                    condition="working",
                    location_type="site",
                    project_id=uuid4(),
                )

    def test_rejects_unknown_enums_and_categories(self):
        base = dict(company_id=uuid4(), name="x", quantity=1, location_type="site", project_id=uuid4())
        with pytest.raises(InvalidInventoryItemError):
            InventoryItem.create(condition="broken", **base)
        with pytest.raises(InvalidInventoryItemError):
            InventoryItem.create(condition="working", category="lasers", **base)
        with pytest.raises(InvalidInventoryItemError):
            InventoryItem.create(company_id=uuid4(), name="x", quantity=1, condition="working", location_type="truck")

    def test_place_id_must_match_location_kind(self):
        with pytest.raises(InvalidInventoryItemError):
            InventoryItem.create(
                company_id=uuid4(), name="x", quantity=1, condition="working", location_type="warehouse"
            )
        with pytest.raises(InvalidInventoryItemError):
            InventoryItem.create(
                company_id=uuid4(),
                name="x",
                quantity=1,
                condition="working",
                location_type="site",
                project_id=uuid4(),
                warehouse_id=uuid4(),
            )

    def test_moving_to_a_site_drops_the_warehouse(self):
        item = InventoryItem.create(
            company_id=uuid4(),
            name="x",
            quantity=1,
            condition="working",
            location_type="warehouse",
            warehouse_id=uuid4(),
        )
        moved = item.with_updates(location_type="site", project_id=uuid4())
        assert moved.warehouse_id is None and moved.project_id is not None

    def test_unchanged_update_returns_same_instance(self):
        item = InventoryItem.create(
            company_id=uuid4(), name="x", quantity=1, condition="working", location_type="site", project_id=uuid4()
        )
        assert item.with_updates(name="x", quantity=1) is item

    def test_blank_optional_strings_become_null(self):
        item = InventoryItem.create(
            company_id=uuid4(),
            name="  Máy mài ",
            quantity=1,
            condition="working",
            location_type="site",
            project_id=uuid4(),
            reference="  ",
            description="",
        )
        assert item.name == "Máy mài" and item.reference is None and item.description is None


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------


class TestWarehouseUseCases:
    def test_member_lists_only_their_company(self, world):
        uc = ListWarehousesUseCase(world["warehouses"], world["membership"])
        names = [w.name for w in uc.execute(requester_id=world["member"], company_id=world["company_a"])]
        assert names == ["Kho Bình Thạnh"]
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(requester_id=world["outsider"], company_id=world["company_a"])

    def test_create_needs_manage_permission(self, world):
        uc = CreateWarehouseUseCase(world["warehouses"], world["membership"], world["checker"], world["session"])
        with pytest.raises(InsufficientPermissionError):
            uc.execute(requester_id=world["member"], company_id=world["company_a"], name="Kho 2")
        created = uc.execute(requester_id=world["manager"], company_id=world["company_a"], name=" Kho 2 ", address=" ")
        assert created.name == "Kho 2" and created.address is None
        assert world["session"].commits == 1

    def test_manage_in_one_company_never_unlocks_another(self, world):
        uc = CreateWarehouseUseCase(world["warehouses"], world["membership"], world["checker"], world["session"])
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(requester_id=world["manager"], company_id=world["company_b"], name="Nope")

    def test_update_only_changes_sent_fields(self, world):
        uc = UpdateWarehouseUseCase(world["warehouses"], world["membership"], world["checker"], world["session"])
        updated = uc.execute(requester_id=world["manager"], warehouse_id=world["wh_a"].id, address=None)
        assert updated.name == "Kho Bình Thạnh" and updated.address is None
        with pytest.raises(WarehouseNotFoundError):
            uc.execute(requester_id=world["manager"], warehouse_id=uuid4(), name="x")

    def test_delete_is_blocked_while_rows_are_stored_there(self, world):
        _drill(world)
        uc = DeleteWarehouseUseCase(
            world["warehouses"], world["items"], world["membership"], world["checker"], world["session"]
        )
        with pytest.raises(WarehouseInUseError):
            uc.execute(requester_id=world["manager"], warehouse_id=world["wh_a"].id)
        for item_id in list(world["items"].rows):
            world["items"].delete(item_id)
        uc.execute(requester_id=world["manager"], warehouse_id=world["wh_a"].id)
        assert world["warehouses"].find_by_id(world["wh_a"].id) is None


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


class TestItemUseCases:
    def test_create_persists_and_commits(self, world):
        item = _drill(world)
        assert world["items"].find_by_id(item.id) == item
        assert world["session"].commits == 1

    def test_create_requires_membership_then_permission(self, world):
        with pytest.raises(CompanyAccessDeniedError):
            _drill(world, requester_id=world["outsider"])
        with pytest.raises(InsufficientPermissionError):
            _drill(world, requester_id=world["member"])

    def test_warehouse_must_belong_to_the_company(self, world):
        with pytest.raises(WarehouseNotFoundError):
            _drill(world, warehouse_id=world["wh_b"].id)
        with pytest.raises(WarehouseNotFoundError):
            _drill(world, warehouse_id=uuid4())

    def test_site_must_be_a_project_of_the_company(self, world):
        with pytest.raises(InvalidInventoryInputError):
            _drill(world, location_type="site", warehouse_id=None, project_id=world["project_b"])
        ok = _drill(world, location_type="site", warehouse_id=None, project_id=world["project_a"])
        assert ok.project_id == world["project_a"] and ok.warehouse_id is None

    def test_invalid_shape_is_a_422_not_a_500(self, world):
        with pytest.raises(InvalidInventoryInputError):
            _drill(world, quantity=-2)
        with pytest.raises(InvalidInventoryInputError):
            _drill(world, location_type="site", warehouse_id=None, project_id=None)

    def test_list_filters_and_search(self, world):
        _drill(world)
        _drill(world, name="Visseuse Makita", quantity=1, condition="damaged", reference=None)
        _drill(
            world,
            name="Thang nhôm",
            reference=None,
            location_type="site",
            warehouse_id=None,
            project_id=world["project_a"],
        )
        uc = ListInventoryItemsUseCase(world["items"], world["membership"])
        all_rows = uc.execute(requester_id=world["member"], company_id=world["company_a"])
        assert [r.name for r in all_rows] == ["Máy khoan Bosch", "Thang nhôm", "Visseuse Makita"]
        assert [
            r.name for r in uc.execute(requester_id=world["member"], company_id=world["company_a"], condition="damaged")
        ] == ["Visseuse Makita"]
        assert [
            r.name
            for r in uc.execute(requester_id=world["member"], company_id=world["company_a"], location_type="site")
        ] == ["Thang nhôm"]
        assert [
            r.name for r in uc.execute(requester_id=world["member"], company_id=world["company_a"], q="sn-778")
        ] == ["Máy khoan Bosch"]
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(requester_id=world["outsider"], company_id=world["company_a"])

    def test_get_checks_membership_of_the_row_company(self, world):
        item = _drill(world)
        uc = GetInventoryItemUseCase(world["items"], world["membership"])
        assert uc.execute(requester_id=world["member"], item_id=item.id).id == item.id
        with pytest.raises(CompanyAccessDeniedError):
            uc.execute(requester_id=world["outsider"], item_id=item.id)
        with pytest.raises(InventoryItemNotFoundError):
            uc.execute(requester_id=world["member"], item_id=uuid4())

    def test_update_marks_damaged_and_moves_to_a_site(self, world):
        item = _drill(world)
        uc = _update_item_uc(world)
        damaged = uc.execute(requester_id=world["manager"], item_id=item.id, condition="damaged")
        assert damaged.condition == "damaged" and damaged.quantity == 3
        moved = uc.execute(
            requester_id=world["manager"], item_id=item.id, location_type="site", project_id=world["project_a"]
        )
        assert moved.location_type == "site" and moved.warehouse_id is None
        with pytest.raises(InvalidInventoryInputError):
            uc.execute(requester_id=world["manager"], item_id=item.id, project_id=world["project_b"])
        with pytest.raises(InsufficientPermissionError):
            uc.execute(requester_id=world["member"], item_id=item.id, quantity=1)

    def test_update_with_nothing_sent_is_a_no_op(self, world):
        item = _drill(world)
        commits_before = world["session"].commits
        same = _update_item_uc(world).execute(requester_id=world["manager"], item_id=item.id, name=UNSET)
        assert same is not None and same.updated_at == item.updated_at
        assert world["session"].commits == commits_before

    def test_delete(self, world):
        item = _drill(world)
        uc = DeleteInventoryItemUseCase(world["items"], world["membership"], world["checker"], world["session"])
        with pytest.raises(InsufficientPermissionError):
            uc.execute(requester_id=world["member"], item_id=item.id)
        uc.execute(requester_id=world["manager"], item_id=item.id)
        assert world["items"].find_by_id(item.id) is None
        with pytest.raises(InventoryItemNotFoundError):
            uc.execute(requester_id=world["manager"], item_id=item.id)
