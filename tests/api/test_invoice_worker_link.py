"""API integration tests for invoice worker_id (labor invoice → worker link).

Covers:
- create labor invoice with worker_id → recipient_name snapshotted from the
  worker's display name (COALESCE(persons.name, workers.name))
- create labor invoice without worker_id → null in response
- create materials_services invoice with worker_id → 400 worker_link_not_allowed
- create/update with a worker from a different project → 400 worker_not_in_project
- create/update with a nonexistent worker → 400 worker_not_in_project (no existence leak)
- PATCH-only worker_id on a labor invoice → updated; all other fields unchanged
- PATCH worker_id=null → cleared
- PATCH type labor→others without touching worker_id → cleared server-side
- list + get responses include worker_id
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.worker import WorkerModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inv_worker_app():
    """Flask app wired with invoice + worker-reader use-cases for worker_id tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_worker_reader import SQLAlchemyWorkerReader
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class InvWorkerTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InvWorkerTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="inv_worker_admin", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="inv_worker_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Worker Link Co",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(
            name="Worker Link Test Project",
            owner_id=admin_user.id,
            company_id=company.id,
        )
        other_project = ProjectModel(
            name="Worker Link Other Project",
            owner_id=admin_user.id,
            company_id=company.id,
        )
        db.session.add_all([project, other_project])
        db.session.flush()

        # Worker with a linked Person — display name must resolve to the
        # Person's name (COALESCE precedence), not the Worker's own name.
        person = PersonModel(
            id=uuid4(),
            name="Jean Dupont",
            normalized_name="jean dupont",
            created_by_user_id=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(person)
        db.session.flush()

        worker_with_person = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            person_id=person.id,
            name="Legacy Worker Name",
            daily_rate=Decimal("150.00"),
            is_active=True,
        )
        # Worker with no linked Person — display name falls back to worker.name.
        worker_no_person = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            name="Marie Curie",
            daily_rate=Decimal("120.00"),
            is_active=True,
        )
        # Worker that belongs to a DIFFERENT project — must be rejected.
        foreign_worker = WorkerModel(
            id=uuid4(),
            project_id=other_project.id,
            name="Foreign Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
        )
        db.session.add_all([worker_with_person, worker_no_person, foreign_worker])
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)
        worker_reader = SQLAlchemyWorkerReader(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _c = get_container()
        _c.worker_reader = worker_reader
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo, worker_reader=worker_reader)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo, worker_reader=worker_reader)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)

        test_app._test_admin_email = "inv_worker_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_worker_with_person_id = str(worker_with_person.id)
        test_app._test_worker_with_person_name = "Jean Dupont"
        test_app._test_worker_no_person_id = str(worker_no_person.id)
        test_app._test_worker_no_person_name = "Marie Curie"
        test_app._test_foreign_worker_id = str(foreign_worker.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_worker_client(inv_worker_app):
    return inv_worker_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_worker_client, inv_worker_app):
    return _login(inv_worker_client, inv_worker_app._test_admin_email, inv_worker_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _labor_invoice_body(**overrides):
    body = {
        "type": "labor",
        "issue_date": date.today().isoformat(),
        "recipient_name": "Worker Payroll",
        "notes": "Original notes",
        "items": [{"description": "Labor", "quantity": 1, "unit_price": 100}],
    }
    body.update(overrides)
    return body


def _create_invoice_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateInvoiceWorkerLink:
    def test_labor_invoice_with_worker_snapshots_recipient_name_from_person(
        self, inv_worker_client, inv_worker_app, admin_token
    ):
        body = _labor_invoice_body(
            worker_id=inv_worker_app._test_worker_with_person_id,
            recipient_name="Ignored client-sent name",
        )
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["worker_id"] == inv_worker_app._test_worker_with_person_id
        assert data["recipient_name"] == inv_worker_app._test_worker_with_person_name

    def test_labor_invoice_with_worker_no_person_uses_worker_name(self, inv_worker_client, inv_worker_app, admin_token):
        body = _labor_invoice_body(worker_id=inv_worker_app._test_worker_no_person_id)
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["recipient_name"] == inv_worker_app._test_worker_no_person_name

    def test_labor_invoice_without_worker_id_is_null(self, inv_worker_client, inv_worker_app, admin_token):
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=_labor_invoice_body(),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["worker_id"] is None

    def test_materials_services_with_worker_id_returns_400(self, inv_worker_client, inv_worker_app, admin_token):
        body = {
            "type": "materials_services",
            "issue_date": date.today().isoformat(),
            "recipient_name": "Supplier Co",
            "items": [{"description": "Materials", "quantity": 1, "unit_price": 50}],
            "worker_id": inv_worker_app._test_worker_with_person_id,
        }
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "worker_link_not_allowed"

    def test_foreign_project_worker_returns_400(self, inv_worker_client, inv_worker_app, admin_token):
        body = _labor_invoice_body(worker_id=inv_worker_app._test_foreign_worker_id)
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "worker_not_in_project"

    def test_nonexistent_worker_returns_400_same_code(self, inv_worker_client, inv_worker_app, admin_token):
        """A worker id that does not exist at all must return the SAME error/code
        as a cross-project worker — never leak whether the id exists elsewhere."""
        body = _labor_invoice_body(worker_id=str(uuid4()))
        resp = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "worker_not_in_project"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdateInvoiceWorkerLink:
    def _create_labor_invoice(self, client, app, token, **overrides):
        body = _labor_invoice_body(**overrides)
        resp = client.post(
            _create_invoice_url(app._test_project_id),
            json=body,
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        return resp.get_json()

    def test_patch_only_worker_id_leaves_other_fields_unchanged(self, inv_worker_client, inv_worker_app, admin_token):
        created = self._create_labor_invoice(
            inv_worker_client,
            inv_worker_app,
            admin_token,
            notes="Keep me",
        )
        invoice_id = created["id"]

        resp = inv_worker_client.put(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            json={"worker_id": inv_worker_app._test_worker_no_person_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()

        assert data["worker_id"] == inv_worker_app._test_worker_no_person_id
        assert data["recipient_name"] == inv_worker_app._test_worker_no_person_name
        # All other fields must be untouched.
        assert data["notes"] == "Keep me"
        assert data["items"] == created["items"]
        assert data["tag_id"] == created["tag_id"]
        assert data["type"] == "labor"

    def test_patch_worker_id_null_clears_it(self, inv_worker_client, inv_worker_app, admin_token):
        created = self._create_labor_invoice(
            inv_worker_client, inv_worker_app, admin_token, worker_id=inv_worker_app._test_worker_no_person_id
        )
        invoice_id = created["id"]
        assert created["worker_id"] == inv_worker_app._test_worker_no_person_id

        resp = inv_worker_client.put(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            json={"worker_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["worker_id"] is None

    def test_patch_type_away_from_labor_clears_stored_worker_id(self, inv_worker_client, inv_worker_app, admin_token):
        created = self._create_labor_invoice(
            inv_worker_client, inv_worker_app, admin_token, worker_id=inv_worker_app._test_worker_no_person_id
        )
        invoice_id = created["id"]
        assert created["worker_id"] == inv_worker_app._test_worker_no_person_id

        # PATCH changes type away from labor without touching worker_id in the payload.
        resp = inv_worker_client.put(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            json={"type": "others"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type"] == "others"
        assert data["worker_id"] is None

    def test_patch_setting_worker_id_on_non_labor_invoice_returns_400(
        self, inv_worker_client, inv_worker_app, admin_token
    ):
        body = {
            "type": "materials_services",
            "issue_date": date.today().isoformat(),
            "recipient_name": "Supplier Co",
            "items": [{"description": "Materials", "quantity": 1, "unit_price": 50}],
        }
        resp_create = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp_create.status_code == 201
        invoice_id = resp_create.get_json()["id"]

        resp = inv_worker_client.put(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            json={"worker_id": inv_worker_app._test_worker_no_person_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "worker_link_not_allowed"

    def test_patch_foreign_project_worker_returns_400(self, inv_worker_client, inv_worker_app, admin_token):
        created = self._create_labor_invoice(inv_worker_client, inv_worker_app, admin_token)
        invoice_id = created["id"]

        resp = inv_worker_client.put(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            json={"worker_id": inv_worker_app._test_foreign_worker_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "worker_not_in_project"

        # Invoice unchanged by the rejected PATCH.
        resp_get = inv_worker_client.get(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            headers=_auth(admin_token),
        )
        assert resp_get.get_json()["worker_id"] is None


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


class TestInvoiceListGetIncludeWorkerId:
    def test_list_and_get_include_worker_id(self, inv_worker_client, inv_worker_app, admin_token):
        # recipient_name is server-overridden by the worker snapshot, so the
        # marker must live in a field the worker link does not touch (notes).
        marker = "WorkerLinkMarker-" + uuid4().hex[:8]
        body = _labor_invoice_body(notes=marker, worker_id=inv_worker_app._test_worker_no_person_id)
        resp_create = inv_worker_client.post(
            _create_invoice_url(inv_worker_app._test_project_id),
            json=body,
            headers=_auth(admin_token),
        )
        assert resp_create.status_code == 201
        invoice_id = resp_create.get_json()["id"]

        resp_list = inv_worker_client.get(
            _create_invoice_url(inv_worker_app._test_project_id),
            headers=_auth(admin_token),
        )
        assert resp_list.status_code == 200
        matching = [i for i in resp_list.get_json()["invoices"] if i["notes"] == marker]
        assert len(matching) == 1
        assert matching[0]["worker_id"] == inv_worker_app._test_worker_no_person_id

        resp_get = inv_worker_client.get(
            _invoice_url(inv_worker_app._test_project_id, invoice_id),
            headers=_auth(admin_token),
        )
        assert resp_get.status_code == 200
        assert resp_get.get_json()["worker_id"] == inv_worker_app._test_worker_no_person_id
