"""Company-scoped capabilities served by the resolver: library + payment methods.

`bibliotheque:manage` and payment-method management reach the resolver through
`RoleCheckerPort`/`ICompanyPermissionChecker` (no project in the URL), so they
answer from the company role: admin yes, manager on an assigned project yes,
member no, platform ops yes.

The library use-cases are wired here rather than in `tests/conftest.py` because
only this module exercises them; everything else (companies, access rows,
payment methods) comes from the shared `invitation_app` fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

PASSWORD = "Biblio1234!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str, password: str = PASSWORD) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def biblio_app(invitation_app):
    """Wire the library use-cases onto the shared app and seed a company manager."""
    from app import db
    from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
    from app.application.bibliotheque.delete_product_usecase import DeleteProductUseCase
    from app.application.bibliotheque.list_products_usecase import ListProductsUseCase
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.company_membership_reader import CompanyMembershipReader
    from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
    from app.infrastructure.database.models import UserModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
    from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_product_repository import (
        SqlAlchemyBibliothequeProductRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_supplier_repository import (
        SqlAlchemyBibliothequeSupplierRepository,
    )
    from app.infrastructure.database.models.associations import user_projects
    from wiring import get_container

    with invitation_app.app_context():
        _c = get_container()
        supplier_repo = SqlAlchemyBibliothequeSupplierRepository(db.session)
        product_repo = SqlAlchemyBibliothequeProductRepository(db.session)
        membership_reader = CompanyMembershipReader(_c.user_company_access_repo)
        checker = _c.authorization_service
        storage = InMemoryDocumentStorage()

        _c.bibliotheque_create_product_usecase = CreateProductUseCase(
            supplier_repo=supplier_repo,
            product_repo=product_repo,
            membership_reader=membership_reader,
            permission_checker=checker,
            db_session=db.session,
        )
        _c.bibliotheque_delete_product_usecase = DeleteProductUseCase(
            product_repo=product_repo,
            image_storage=storage,
            membership_reader=membership_reader,
            permission_checker=checker,
            db_session=db.session,
        )
        _c.bibliotheque_list_products_usecase = ListProductsUseCase(
            product_repo=product_repo,
            membership_reader=membership_reader,
        )

        # A manager assigned to a project of the company: the matrix gives them
        # bibliotheque:manage exactly like an admin.
        now = datetime.now(timezone.utc)
        company_uuid = UUID(invitation_app._test_company_id)
        project_uuid = UUID(invitation_app._test_project_id)
        hasher = Argon2PasswordHasher()
        manager = UserModel(
            id=uuid4(), email="biblio_manager@test.com", password_hash=hasher.hash(PASSWORD), is_active=True
        )
        unassigned = UserModel(
            id=uuid4(), email="biblio_unassigned@test.com", password_hash=hasher.hash(PASSWORD), is_active=True
        )
        db.session.add_all([manager, unassigned])
        db.session.flush()
        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=manager.id,
                    company_id=company_uuid,
                    role="manager",
                    is_primary=True,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=unassigned.id,
                    company_id=company_uuid,
                    role="manager",
                    is_primary=True,
                    attached_at=now,
                ),
            ]
        )
        db.session.execute(user_projects.insert().values(user_id=manager.id, project_id=project_uuid, assigned_at=now))
        db.session.commit()

    return invitation_app


@pytest.fixture
def biblio_client(biblio_app):
    return biblio_app.test_client()


@pytest.fixture
def manager_token(biblio_client):
    return _login(biblio_client, "biblio_manager@test.com")


@pytest.fixture
def unassigned_manager_token(biblio_client):
    return _login(biblio_client, "biblio_unassigned@test.com")


def _product_payload(company_id: str, name: str, supplier: str) -> dict:
    return {"company_id": company_id, "name": name, "supplier_name": supplier}


class TestBibliothequeWrites:
    def test_company_admin_creates_a_product(self, biblio_client, biblio_app, admin_token):
        resp = biblio_client.post(
            "/api/v1/bibliotheque/products",
            json=_product_payload(biblio_app._test_company_id, "Admin Product", "Admin Supplier"),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_assigned_manager_creates_a_product(self, biblio_client, biblio_app, manager_token):
        resp = biblio_client.post(
            "/api/v1/bibliotheque/products",
            json=_product_payload(biblio_app._test_company_id, "Manager Product", "Manager Supplier"),
            headers=_auth(manager_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_member_is_forbidden(self, biblio_client, biblio_app, member_token):
        resp = biblio_client.post(
            "/api/v1/bibliotheque/products",
            json=_product_payload(biblio_app._test_company_id, "Member Product", "Member Supplier"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_manager_without_any_assignment_is_forbidden(self, biblio_client, biblio_app, unassigned_manager_token):
        """A manager assigned to nothing manages nothing — the matrix, not the role name."""
        resp = biblio_client.post(
            "/api/v1/bibliotheque/products",
            json=_product_payload(biblio_app._test_company_id, "Unassigned Product", "Unassigned Supplier"),
            headers=_auth(unassigned_manager_token),
        )
        assert resp.status_code == 403


class TestPaymentMethodManagement:
    def _url(self, company_id: str) -> str:
        return f"/api/v1/companies/{company_id}/payment-methods"

    def test_company_admin_creates_one(self, biblio_client, biblio_app, admin_token):
        resp = biblio_client.post(
            self._url(biblio_app._test_company_id), json={"label": "Admin Card"}, headers=_auth(admin_token)
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_platform_ops_creates_one(self, biblio_client, biblio_app, superadmin_token):
        resp = biblio_client.post(
            self._url(biblio_app._test_company_id), json={"label": "Ops Card"}, headers=_auth(superadmin_token)
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_member_is_forbidden(self, biblio_client, biblio_app, member_token):
        resp = biblio_client.post(
            self._url(biblio_app._test_company_id), json={"label": "Member Card"}, headers=_auth(member_token)
        )
        assert resp.status_code == 403

    def test_manager_is_forbidden(self, biblio_client, biblio_app, manager_token):
        """Payment methods are company settings: admin (or ops) only, never a manager."""
        resp = biblio_client.post(
            self._url(biblio_app._test_company_id), json={"label": "Manager Card"}, headers=_auth(manager_token)
        )
        assert resp.status_code == 403
