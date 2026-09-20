"""Integration tests for the inventory API — /api/v1/inventory/{warehouses,items}.

Company roles drive `inventory:manage` exactly like `bibliotheque:manage`: admin
company-wide, manager on an assigned project, member never, outsider nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.associations import user_projects
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def inventory_app():
    from app import create_app, db
    from app.application.inventory.item_usecases import (
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
    from app.infrastructure.adapters.company_membership_reader import CompanyMembershipReader
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.project_company_reader import ProjectCompanyReader
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_inventory_repository import (
        SqlAlchemyInventoryItemRepository,
        SqlAlchemyInventoryWarehouseRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from config import TestingConfig
    from wiring import configure_container, get_container

    class InventoryTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InventoryTestConfig)

    with test_app.app_context():
        db.create_all()

        admin_user = UserModel(email="inv_admin@test.com", is_active=True)
        member_user = UserModel(email="inv_member@test.com", is_active=True)
        manager_user = UserModel(email="inv_manager@test.com", is_active=True)
        outsider_user = UserModel(email="inv_outsider@test.com", is_active=True)
        db.session.add_all([admin_user, member_user, manager_user, outsider_user])
        db.session.flush()

        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="Inventory Test Company",
            address="1 Rue du Chantier",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        other_company = CompanyModel(
            id=uuid4(),
            legal_name="Other Company",
            address="2 Rue Ailleurs",
            created_by=outsider_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company, other_company])
        db.session.flush()

        for user, role in ((admin_user, "admin"), (manager_user, "manager"), (member_user, "member")):
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user.id, company_id=company.id, role=role, is_primary=True, attached_at=now
                )
            )
        db.session.add(
            UserCompanyAccessModel(
                user_id=outsider_user.id, company_id=other_company.id, role="admin", is_primary=True, attached_at=now
            )
        )

        project = ProjectModel(id=uuid4(), name="Villa Thảo Điền", owner_id=admin_user.id, company_id=company.id)
        foreign_project = ProjectModel(
            id=uuid4(), name="Elsewhere", owner_id=outsider_user.id, company_id=other_company.id
        )
        db.session.add_all([project, foreign_project])
        db.session.flush()
        db.session.execute(
            user_projects.insert().values(user_id=manager_user.id, project_id=project.id, assigned_at=now)
        )
        db.session.execute(
            user_projects.insert().values(user_id=member_user.id, project_id=project.id, assigned_at=now)
        )
        db.session.commit()

        configure_container(
            user_repository=SQLAlchemyUserRepository(db.session),
            project_repository=SQLAlchemyProjectRepository(db.session),
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
        )

        _c = get_container()
        warehouse_repo = SqlAlchemyInventoryWarehouseRepository(db.session)
        item_repo = SqlAlchemyInventoryItemRepository(db.session)
        membership = CompanyMembershipReader(SqlAlchemyUserCompanyAccessRepository(db.session))
        checker = _c.authorization_service
        projects = ProjectCompanyReader(db.session)

        _c.inventory_warehouse_repo = warehouse_repo
        _c.inventory_item_repo = item_repo
        _c.inventory_list_warehouses_usecase = ListWarehousesUseCase(warehouse_repo, membership)
        _c.inventory_create_warehouse_usecase = CreateWarehouseUseCase(warehouse_repo, membership, checker, db.session)
        _c.inventory_update_warehouse_usecase = UpdateWarehouseUseCase(warehouse_repo, membership, checker, db.session)
        _c.inventory_delete_warehouse_usecase = DeleteWarehouseUseCase(
            warehouse_repo, item_repo, membership, checker, db.session
        )
        _c.inventory_list_items_usecase = ListInventoryItemsUseCase(item_repo, membership)
        _c.inventory_get_item_usecase = GetInventoryItemUseCase(item_repo, membership)
        _c.inventory_create_item_usecase = CreateInventoryItemUseCase(
            item_repo, warehouse_repo, projects, membership, checker, db.session
        )
        _c.inventory_update_item_usecase = UpdateInventoryItemUseCase(
            item_repo, warehouse_repo, projects, membership, checker, db.session
        )
        _c.inventory_delete_item_usecase = DeleteInventoryItemUseCase(item_repo, membership, checker, db.session)

        test_app._inv = {
            "company_id": str(company.id),
            "other_company_id": str(other_company.id),
            "project_id": str(project.id),
            "foreign_project_id": str(foreign_project.id),
        }

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(inventory_app):
    return inventory_app.test_client()


def _auth(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


@pytest.fixture
def admin(client):
    return _auth(client, "inv_admin@test.com")


@pytest.fixture
def manager(client):
    return _auth(client, "inv_manager@test.com")


@pytest.fixture
def member(client):
    return _auth(client, "inv_member@test.com")


@pytest.fixture
def outsider(client):
    return _auth(client, "inv_outsider@test.com")


def _warehouse(client, headers, company_id: str, name: str = "Kho Bình Thạnh", address: str | None = "12 NHC") -> dict:
    resp = client.post(
        "/api/v1/inventory/warehouses",
        json={"company_id": company_id, "name": name, "address": address},
        headers=headers,
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _item(client, headers, company_id: str, **overrides) -> dict:
    body = {
        "company_id": company_id,
        "name": "Máy khoan Bosch",
        "category": "power_tool",
        "reference": "SN-778",
        "quantity": 3,
        "condition": "working",
        "location_type": "warehouse",
    }
    body.update(overrides)
    return client.post("/api/v1/inventory/items", json=body, headers=headers)


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------


class TestWarehouses:
    def test_401_unauthenticated(self, client, inventory_app):
        assert (
            client.get(f"/api/v1/inventory/warehouses?company_id={inventory_app._inv['company_id']}").status_code == 401
        )

    def test_member_lists_outsider_is_refused(self, client, member, outsider, inventory_app):
        cid = inventory_app._inv["company_id"]
        assert client.get(f"/api/v1/inventory/warehouses?company_id={cid}", headers=member).status_code == 200
        assert client.get(f"/api/v1/inventory/warehouses?company_id={cid}", headers=outsider).status_code == 403
        assert client.get("/api/v1/inventory/warehouses?company_id=nope", headers=member).status_code == 422

    def test_missing_company_id_falls_back_to_the_primary_company(self, client, member):
        resp = client.get("/api/v1/inventory/warehouses", headers=member)
        assert resp.status_code == 200
        assert "items" in resp.get_json()

    def test_create_edit_delete_lifecycle(self, client, admin, member, inventory_app):
        cid = inventory_app._inv["company_id"]
        created = _warehouse(client, admin, cid, name=" Kho A ", address="  ")
        assert created["name"] == "Kho A" and created["address"] is None

        # member: read yes, write no
        assert (
            client.post(
                "/api/v1/inventory/warehouses", json={"company_id": cid, "name": "X"}, headers=member
            ).status_code
            == 403
        )

        patched = client.patch(
            f"/api/v1/inventory/warehouses/{created['id']}", json={"address": "45 Võ Văn Ngân"}, headers=admin
        )
        assert patched.status_code == 200
        assert patched.get_json()["address"] == "45 Võ Văn Ngân" and patched.get_json()["name"] == "Kho A"

        listed = client.get(f"/api/v1/inventory/warehouses?company_id={cid}", headers=member).get_json()["items"]
        assert any(w["id"] == created["id"] for w in listed)

        assert client.delete(f"/api/v1/inventory/warehouses/{created['id']}", headers=member).status_code == 403
        assert client.delete(f"/api/v1/inventory/warehouses/{created['id']}", headers=admin).status_code == 204
        assert client.delete(f"/api/v1/inventory/warehouses/{created['id']}", headers=admin).status_code == 404

    def test_manager_on_an_assigned_project_may_write(self, client, manager, inventory_app):
        created = _warehouse(client, manager, inventory_app._inv["company_id"], name="Kho của quản lý")
        assert client.delete(f"/api/v1/inventory/warehouses/{created['id']}", headers=manager).status_code == 204

    def test_outsider_cannot_write_into_another_company(self, client, outsider, inventory_app):
        resp = client.post(
            "/api/v1/inventory/warehouses",
            json={"company_id": inventory_app._inv["company_id"], "name": "Intrus"},
            headers=outsider,
        )
        assert resp.status_code == 403

    def test_delete_is_refused_while_rows_are_stored_there(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho đầy")
        item = _item(client, admin, cid, warehouse_id=warehouse["id"])
        assert item.status_code == 201, item.get_json()
        blocked = client.delete(f"/api/v1/inventory/warehouses/{warehouse['id']}", headers=admin)
        assert blocked.status_code == 409
        assert blocked.get_json()["error"] == "Conflict"
        assert client.delete(f"/api/v1/inventory/items/{item.get_json()['id']}", headers=admin).status_code == 204
        assert client.delete(f"/api/v1/inventory/warehouses/{warehouse['id']}", headers=admin).status_code == 204

    def test_422_on_bad_body(self, client, admin, inventory_app):
        resp = client.post(
            "/api/v1/inventory/warehouses",
            json={"company_id": inventory_app._inv["company_id"], "name": "", "extra": 1},
            headers=admin,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


class TestItems:
    def test_create_at_a_warehouse_and_read_back(self, client, admin, member, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho đọc")
        resp = _item(client, admin, cid, warehouse_id=warehouse["id"], description="  ")
        assert resp.status_code == 201, resp.get_json()
        row = resp.get_json()
        assert row["quantity"] == 3 and row["condition"] == "working" and row["warehouse_id"] == warehouse["id"]
        assert row["project_id"] is None and row["description"] is None and row["category"] == "power_tool"

        got = client.get(f"/api/v1/inventory/items/{row['id']}", headers=member)
        assert got.status_code == 200 and got.get_json()["name"] == "Máy khoan Bosch"

    def test_create_on_a_site_of_the_company(self, client, manager, inventory_app):
        cid = inventory_app._inv["company_id"]
        resp = _item(
            client,
            manager,
            cid,
            name="Visseuse Makita",
            location_type="site",
            project_id=inventory_app._inv["project_id"],
            condition="damaged",
            quantity=1,
        )
        assert resp.status_code == 201, resp.get_json()
        assert resp.get_json()["location_type"] == "site" and resp.get_json()["warehouse_id"] is None

    def test_place_outside_the_company_is_refused(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        foreign_site = _item(
            client, admin, cid, location_type="site", project_id=inventory_app._inv["foreign_project_id"]
        )
        assert foreign_site.status_code == 422
        unknown_warehouse = _item(client, admin, cid, warehouse_id=str(uuid4()))
        assert unknown_warehouse.status_code == 404

    def test_validation_422(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho v")
        assert _item(client, admin, cid, warehouse_id=warehouse["id"], quantity=-1).status_code == 422
        assert _item(client, admin, cid, warehouse_id=warehouse["id"], quantity=1.5).status_code == 422
        assert _item(client, admin, cid, warehouse_id=warehouse["id"], condition="broken").status_code == 422
        assert _item(client, admin, cid, warehouse_id=warehouse["id"], category="lasers").status_code == 422
        assert _item(client, admin, cid, location_type="warehouse").status_code == 422  # no warehouse_id
        assert _item(client, admin, cid, location_type="site").status_code == 422  # no project_id

    def test_member_reads_but_cannot_write(self, client, admin, member, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho m")
        assert _item(client, member, cid, warehouse_id=warehouse["id"]).status_code == 403
        created = _item(client, admin, cid, warehouse_id=warehouse["id"]).get_json()
        assert (
            client.patch(f"/api/v1/inventory/items/{created['id']}", json={"quantity": 1}, headers=member).status_code
            == 403
        )
        assert client.delete(f"/api/v1/inventory/items/{created['id']}", headers=member).status_code == 403
        assert client.get(f"/api/v1/inventory/items?company_id={cid}", headers=member).status_code == 200

    def test_outsider_sees_nothing(self, client, admin, outsider, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho o")
        created = _item(client, admin, cid, warehouse_id=warehouse["id"]).get_json()
        assert client.get(f"/api/v1/inventory/items?company_id={cid}", headers=outsider).status_code == 403
        assert client.get(f"/api/v1/inventory/items/{created['id']}", headers=outsider).status_code == 403
        assert (
            client.patch(f"/api/v1/inventory/items/{created['id']}", json={"quantity": 0}, headers=outsider).status_code
            == 403
        )

    def test_list_filters(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho lọc")
        _item(client, admin, cid, name="Filter drill", reference="FLT-1", warehouse_id=warehouse["id"])
        _item(
            client,
            admin,
            cid,
            name="Filter ladder",
            reference="FLT-2",
            location_type="site",
            project_id=inventory_app._inv["project_id"],
            condition="damaged",
            category="access",
        )

        def names(query: str) -> list[str]:
            resp = client.get(f"/api/v1/inventory/items?company_id={cid}&{query}", headers=admin)
            assert resp.status_code == 200, resp.get_json()
            return [i["name"] for i in resp.get_json()["items"]]

        assert names("q=flt-") == ["Filter drill", "Filter ladder"]
        assert names("q=flt-&condition=damaged") == ["Filter ladder"]
        assert names("q=flt-&location_type=site") == ["Filter ladder"]
        assert names(f"q=flt-&warehouse_id={warehouse['id']}") == ["Filter drill"]
        assert names(f"q=flt-&project_id={inventory_app._inv['project_id']}") == ["Filter ladder"]
        assert (
            client.get(f"/api/v1/inventory/items?company_id={cid}&condition=broken", headers=admin).status_code == 422
        )
        assert client.get(f"/api/v1/inventory/items?company_id={cid}&warehouse_id=x", headers=admin).status_code == 422

    def test_patch_is_a_diff_and_can_move_the_row(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho patch")
        created = _item(client, admin, cid, warehouse_id=warehouse["id"]).get_json()

        damaged = client.patch(f"/api/v1/inventory/items/{created['id']}", json={"condition": "damaged"}, headers=admin)
        assert damaged.status_code == 200
        assert damaged.get_json()["condition"] == "damaged" and damaged.get_json()["quantity"] == 3

        moved = client.patch(
            f"/api/v1/inventory/items/{created['id']}",
            json={"location_type": "site", "project_id": inventory_app._inv["project_id"]},
            headers=admin,
        )
        assert moved.status_code == 200
        assert moved.get_json()["location_type"] == "site" and moved.get_json()["warehouse_id"] is None

        cleared = client.patch(f"/api/v1/inventory/items/{created['id']}", json={"reference": None}, headers=admin)
        assert cleared.status_code == 200 and cleared.get_json()["reference"] is None

        foreign = client.patch(
            f"/api/v1/inventory/items/{created['id']}",
            json={"project_id": inventory_app._inv["foreign_project_id"]},
            headers=admin,
        )
        assert foreign.status_code == 422
        assert (
            client.patch(f"/api/v1/inventory/items/{uuid4()}", json={"quantity": 1}, headers=admin).status_code == 404
        )
        assert (
            client.patch(f"/api/v1/inventory/items/{created['id']}", json={"nope": 1}, headers=admin).status_code == 422
        )

    def test_delete(self, client, admin, inventory_app):
        cid = inventory_app._inv["company_id"]
        warehouse = _warehouse(client, admin, cid, name="Kho xoá")
        created = _item(client, admin, cid, warehouse_id=warehouse["id"]).get_json()
        assert client.delete(f"/api/v1/inventory/items/{created['id']}", headers=admin).status_code == 204
        assert client.get(f"/api/v1/inventory/items/{created['id']}", headers=admin).status_code == 404
        assert client.delete(f"/api/v1/inventory/items/{created['id']}", headers=admin).status_code == 404


# ---------------------------------------------------------------------------
# The permission shows up where the clients read it
# ---------------------------------------------------------------------------


class TestPermissionClaim:
    def test_admin_and_manager_hold_inventory_manage_member_does_not(self, client, admin, manager, member):
        for headers, expected in ((admin, True), (manager, True), (member, False)):
            resp = client.get("/api/v1/auth/me", headers=headers)
            assert resp.status_code == 200
            assert ("inventory:manage" in resp.get_json()["permissions"]) is expected
