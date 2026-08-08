"""API integration tests for avoir-settled return invoices (applied_to_invoice_id + settled_via).

Covers:
- create return settled_via=avoir + applied_to → 201, payment_method_id auto-aligned
  to the target's (including alignment to NULL)
- 400 cases: applied_to on non-return type, cross-project target, target type
  return/released_funds, self-link on update, cap breach, applied_to with
  settled_via null/cash, settled_via on non-return type
- PATCH only settled_via → other fields intact (description-drop regression)
- PATCH clearing applied_to_invoice_id → payment method left untouched
- list response: settled_via + applied_to_invoice_number + paid_with_returns
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def avoir_app():
    """Flask app wired with invoice + payment_method use-cases for avoir-link tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
        SqlAlchemyPaymentMethodRepository,
    )
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.update_invoice import UpdateInvoiceUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class AvoirTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AvoirTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")

        admin_role = RoleModel(name="avoir_admin", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)

        db.session.add_all([star_perm, read_perm, manage_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="avoir_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Avoir Co SARL",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        # Two projects under the same company so cross-project link rejection can be
        # exercised: applying a return in project_1 to a target in project_2 must 400.
        project_1 = ProjectModel(name="Avoir Project 1", owner_id=admin_user.id, company_id=company.id)
        project_2 = ProjectModel(name="Avoir Project 2", owner_id=admin_user.id, company_id=company.id)
        db.session.add_all([project_1, project_2])
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _c = get_container()
        _pm_repo = SqlAlchemyPaymentMethodRepository(db.session)
        _c.payment_method_repo = _pm_repo

        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.update_invoice_usecase = UpdateInvoiceUseCase(invoice_repo=invoice_repo, payment_method_repo=_pm_repo)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)

        test_app._test_admin_email = "avoir_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_company_id = str(company.id)
        test_app._test_project_id = str(project_1.id)
        test_app._test_project_2_id = str(project_2.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def avoir_client(avoir_app):
    return avoir_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(avoir_client, avoir_app):
    return _login(avoir_client, avoir_app._test_admin_email, avoir_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_pm_row(app, label="Wire Transfer"):
    """Insert an active PaymentMethodModel for the fixture's company and return its id."""
    from app import db

    with app.app_context():
        now = datetime.now(timezone.utc)
        row = PaymentMethodModel(
            id=uuid4(),
            company_id=UUID(app._test_company_id),
            label=label,
            is_builtin=False,
            is_active=True,
            created_by=UUID(app._test_admin_user_id),
            created_at=now,
            updated_at=now,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


def _invoices_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _invoice_url(project_id, invoice_id):
    return f"/api/v1/projects/{project_id}/invoices/{invoice_id}"


def _invoice_body(invoice_type="materials_services", unit_price=100.0, recipient_name="Supplier Co", **extra):
    body = {
        "type": invoice_type,
        "issue_date": date.today().isoformat(),
        "recipient_name": recipient_name,
        "items": [{"description": "Line item", "quantity": 1, "unit_price": unit_price}],
    }
    body.update(extra)
    return body


def _create(client, app, token, project_id=None, **body_overrides):
    resp = client.post(
        _invoices_url(project_id or app._test_project_id),
        json=_invoice_body(**body_overrides),
        headers=_auth(token),
    )
    return resp


def _create_target(client, app, token, amount=500.0, pm_id=None, project_id=None, invoice_type="materials_services"):
    """Create a target invoice (e.g. materials_services) eligible to receive an avoir link."""
    overrides = {}
    if pm_id is not None:
        overrides["payment_method_id"] = pm_id
    resp = _create(client, app, token, project_id=project_id, invoice_type=invoice_type, unit_price=amount, **overrides)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _create_return(client, app, token, applied_to=None, settled_via=None, unit_price=-100.0, project_id=None):
    overrides = {}
    if settled_via is not None:
        overrides["settled_via"] = settled_via
    if applied_to is not None:
        overrides["applied_to_invoice_id"] = applied_to
    return _create(client, app, token, project_id=project_id, invoice_type="return", unit_price=unit_price, **overrides)


# ---------------------------------------------------------------------------
# Create: happy path + auto-align
# ---------------------------------------------------------------------------


class TestCreateAvoirLinkedReturn:
    def test_create_avoir_return_aligns_payment_method_to_target(self, avoir_client, avoir_app, admin_token):
        pm_id = _make_pm_row(avoir_app, label="Wire A")
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0, pm_id=pm_id)

        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-200.0
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["settled_via"] == "avoir"
        assert data["applied_to_invoice_id"] == target["id"]
        assert data["applied_to_invoice_number"] == target["invoice_number"]
        # Auto-aligned to the target's payment method, not whatever the caller sent.
        assert data["payment_method_id"] == pm_id
        assert data["payment_method_label"] == "Wire A"

    def test_create_avoir_return_aligns_to_null_when_target_has_no_method(self, avoir_client, avoir_app, admin_token):
        other_pm_id = _make_pm_row(avoir_app, label="Should Be Ignored")
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0, pm_id=None)

        resp = avoir_client.post(
            _invoices_url(avoir_app._test_project_id),
            json={
                **_invoice_body(invoice_type="return", unit_price=-100.0),
                "settled_via": "avoir",
                "applied_to_invoice_id": target["id"],
                # Caller-supplied payment_method_id must be overridden by the auto-align.
                "payment_method_id": other_pm_id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["payment_method_id"] is None
        assert data["payment_method_label"] is None

    def test_create_return_cash_settled_without_link_succeeds(self, avoir_client, avoir_app, admin_token):
        """settled_via='cash' with no applied_to_invoice_id is a plain valid return."""
        resp = _create_return(avoir_client, avoir_app, admin_token, settled_via="cash", unit_price=-50.0)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["settled_via"] == "cash"
        assert data["applied_to_invoice_id"] is None


# ---------------------------------------------------------------------------
# Create: validation (400 cases)
# ---------------------------------------------------------------------------


class TestCreateAvoirLinkValidation:
    def test_applied_to_on_non_return_type_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        resp = _create(
            avoir_client,
            avoir_app,
            admin_token,
            invoice_type="materials_services",
            applied_to_invoice_id=target["id"],
            settled_via="avoir",
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_settled_via_on_non_return_type_400(self, avoir_client, avoir_app, admin_token):
        resp = _create(avoir_client, avoir_app, admin_token, invoice_type="materials_services", settled_via="avoir")
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_nonexistent_target_400(self, avoir_client, avoir_app, admin_token):
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=str(uuid4()), settled_via="avoir", unit_price=-100.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_cross_project_target_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(
            avoir_client, avoir_app, admin_token, amount=500.0, project_id=avoir_app._test_project_2_id
        )
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_target_type_return_400(self, avoir_client, avoir_app, admin_token):
        other_return = _create_return(avoir_client, avoir_app, admin_token, settled_via="cash", unit_price=-50.0)
        assert other_return.status_code == 201
        other_return_id = other_return.get_json()["id"]

        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=other_return_id, settled_via="avoir", unit_price=-10.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_target_type_released_funds_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0, invoice_type="released_funds")
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_with_settled_via_null_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        resp = _create_return(avoir_client, avoir_app, admin_token, applied_to=target["id"], unit_price=-100.0)
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_applied_to_with_settled_via_cash_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="cash", unit_price=-100.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_cap_breach_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=300.0)
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-400.0
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "AppliedExceedsTarget"

    def test_cap_at_exact_limit_succeeds(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=300.0)
        resp = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-300.0
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_second_return_cap_breach_accounts_for_first(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=300.0)
        first = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-200.0
        )
        assert first.status_code == 201, first.get_data(as_text=True)

        second = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-150.0
        )
        assert second.status_code == 400, second.get_data(as_text=True)
        assert second.get_json()["error"] == "AppliedExceedsTarget"


# ---------------------------------------------------------------------------
# Update: self-link, clearing, and the description-drop regression
# ---------------------------------------------------------------------------


class TestUpdateAvoirLink:
    def test_self_link_on_update_400(self, avoir_client, avoir_app, admin_token):
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-50.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": invoice_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_only_settled_via_leaves_other_fields_intact(self, avoir_client, avoir_app, admin_token):
        """Regression: PATCHing ONLY settled_via must not drop unrelated fields
        (the update-usecase description-drop landmine)."""
        created = _create_return(
            avoir_client, avoir_app, admin_token, settled_via="cash", unit_price=-75.0, project_id=None
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        before = created.get_json()
        invoice_id = before["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"settled_via": "avoir"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        after = resp.get_json()

        assert after["settled_via"] == "avoir"
        # Everything else must survive untouched.
        assert after["recipient_name"] == before["recipient_name"]
        assert after["items"] == before["items"]
        assert after["total_amount"] == before["total_amount"]
        assert after["issue_date"] == before["issue_date"]
        assert after["notes"] == before["notes"]
        assert after["applied_to_invoice_id"] == before["applied_to_invoice_id"]

    def test_patch_clearing_applied_to_keeps_payment_method(self, avoir_client, avoir_app, admin_token):
        pm_id = _make_pm_row(avoir_app, label="Wire Clear Test")
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0, pm_id=pm_id)
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.0
        )
        assert created.status_code == 201
        before = created.get_json()
        assert before["payment_method_id"] == pm_id
        invoice_id = before["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        after = resp.get_json()
        assert after["applied_to_invoice_id"] is None
        # Method is deliberately left untouched when the link is merely cleared.
        assert after["payment_method_id"] == pm_id
        assert after["payment_method_label"] == "Wire Clear Test"

    def test_patch_set_link_auto_aligns_payment_method(self, avoir_client, avoir_app, admin_token):
        pm_id = _make_pm_row(avoir_app, label="Wire Update Align")
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0, pm_id=pm_id)
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-100.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]
        assert created.get_json()["payment_method_id"] is None

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": target["id"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["applied_to_invoice_id"] == target["id"]
        assert data["payment_method_id"] == pm_id

    def test_patch_cap_breach_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=200.0)
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-150.0
        )
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        # Raising this return's own amount past the remaining 50 must 400.
        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"items": [{"description": "Line item", "quantity": 1, "unit_price": -250.0}]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "AppliedExceedsTarget"


class TestUpdateAvoirLinkValidation:
    """400 cases for settled_via/applied_to_invoice_id validation exercised via PATCH,
    mirroring the create-time checks (TestCreateAvoirLinkValidation) at the update path."""

    def test_patch_clears_settled_via_explicit_null(self, avoir_client, avoir_app, admin_token):
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="cash", unit_price=-50.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"settled_via": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["settled_via"] is None

    def test_patch_settled_via_on_non_return_type_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=200.0)
        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, target["id"]),
            json={"settled_via": "avoir"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_applied_to_on_non_return_type_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=200.0)
        other_target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, target["id"]),
            json={"applied_to_invoice_id": other_target["id"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_applied_to_with_settled_via_cash_in_same_patch_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        created = _create_return(avoir_client, avoir_app, admin_token, unit_price=-100.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"settled_via": "cash", "applied_to_invoice_id": target["id"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_applied_to_nonexistent_target_400(self, avoir_client, avoir_app, admin_token):
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-100.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": str(uuid4())},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_applied_to_cross_project_target_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(
            avoir_client, avoir_app, admin_token, amount=500.0, project_id=avoir_app._test_project_2_id
        )
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-100.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": target["id"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_applied_to_target_type_return_400(self, avoir_client, avoir_app, admin_token):
        other_return = _create_return(avoir_client, avoir_app, admin_token, settled_via="cash", unit_price=-30.0)
        assert other_return.status_code == 201
        other_return_id = other_return.get_json()["id"]

        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-10.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": other_return_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_patch_set_new_link_cap_breach_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=200.0)
        created = _create_return(avoir_client, avoir_app, admin_token, settled_via="avoir", unit_price=-250.0)
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"applied_to_invoice_id": target["id"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "AppliedExceedsTarget"

    def test_patch_settled_via_away_from_avoir_while_link_remains_400(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.0
        )
        assert created.status_code == 201
        invoice_id = created.get_json()["id"]

        # settled_via changing away from 'avoir' while applied_to_invoice_id is left
        # untouched (still set) is an invalid final state — the caller must explicitly
        # clear the link in the same PATCH.
        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"settled_via": "cash"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# List / get enrichment
# ---------------------------------------------------------------------------


class TestListAndGetEnrichment:
    def test_list_includes_settled_via_applied_number_and_paid_with_returns(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        return_a = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-200.0
        )
        return_b = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.0
        )
        assert return_a.status_code == 201, return_a.get_data(as_text=True)
        assert return_b.status_code == 201, return_b.get_data(as_text=True)
        return_a_data = return_a.get_json()
        return_b_data = return_b.get_json()

        resp = avoir_client.get(_invoices_url(avoir_app._test_project_id), headers=_auth(admin_token))
        assert resp.status_code == 200
        invoices = resp.get_json()["invoices"]

        target_row = next(i for i in invoices if i["id"] == target["id"])
        by_number = {r["invoice_number"]: r for r in target_row["paid_with_returns"]}
        assert set(by_number.keys()) == {return_a_data["invoice_number"], return_b_data["invoice_number"]}
        assert by_number[return_a_data["invoice_number"]]["total_amount"] == -200.0
        assert by_number[return_b_data["invoice_number"]]["total_amount"] == -100.0

        return_a_row = next(i for i in invoices if i["id"] == return_a_data["id"])
        assert return_a_row["settled_via"] == "avoir"
        assert return_a_row["applied_to_invoice_id"] == target["id"]
        assert return_a_row["applied_to_invoice_number"] == target["invoice_number"]

    def test_get_includes_paid_with_returns_and_applied_to_number(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-150.0
        )
        assert created.status_code == 201
        return_data = created.get_json()

        resp = avoir_client.get(_invoice_url(avoir_app._test_project_id, target["id"]), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["paid_with_returns"]) == 1
        assert data["paid_with_returns"][0]["invoice_number"] == return_data["invoice_number"]
        assert data["paid_with_returns"][0]["total_amount"] == -150.0

        resp_return = avoir_client.get(
            _invoice_url(avoir_app._test_project_id, return_data["id"]), headers=_auth(admin_token)
        )
        assert resp_return.status_code == 200
        return_get_data = resp_return.get_json()
        assert return_get_data["applied_to_invoice_number"] == target["invoice_number"]
        assert return_get_data["settled_via"] == "avoir"

    def test_paid_with_returns_total_amount_is_quantized_to_cents(self, avoir_client, avoir_app, admin_token):
        """NIT regression: applied_return_summaries must pass amounts through the house
        money() quantizer, not raw float(Decimal), so sub-cent artifacts never leak into
        paid_with_returns JSON."""
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        # unit_price carries a sub-cent fraction; ROUND_HALF_UP must produce -100.01.
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-100.005
        )
        assert created.status_code == 201, created.get_data(as_text=True)

        resp = avoir_client.get(_invoice_url(avoir_app._test_project_id, target["id"]), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["paid_with_returns"]) == 1
        assert data["paid_with_returns"][0]["total_amount"] == -100.01


# ---------------------------------------------------------------------------
# Update: type change away from 'return' must not strand avoir/refund link fields (H1)
# ---------------------------------------------------------------------------


class TestUpdateTypeChangeClearsReturnLinkFields:
    """H1 regression: PATCHing type away from 'return' must auto-clear settled_via,
    applied_to_invoice_id, and refunds_invoice_id — mirroring the pre-existing
    service_month auto-clear pattern one screen up in the same use-case. Without this,
    the stranded applied_to_invoice_id silently keeps consuming the target's cap and
    changing type back to 'return' later would otherwise resurrect the link."""

    def test_patch_type_away_clears_all_link_fields_and_frees_target_cap(self, avoir_client, avoir_app, admin_token):
        # Independent materials_services source for refunds_invoice_id, with enough
        # headroom that it is not the constraint under test.
        refund_source = _create_target(avoir_client, avoir_app, admin_token, amount=1000.0)
        applied_target = _create_target(avoir_client, avoir_app, admin_token, amount=300.0)

        created = avoir_client.post(
            _invoices_url(avoir_app._test_project_id),
            json={
                **_invoice_body(invoice_type="return", unit_price=-300.0),
                "refunds_invoice_id": refund_source["id"],
                "settled_via": "avoir",
                "applied_to_invoice_id": applied_target["id"],
            },
            headers=_auth(admin_token),
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        before = created.get_json()
        assert before["refunds_invoice_id"] == refund_source["id"]
        assert before["settled_via"] == "avoir"
        assert before["applied_to_invoice_id"] == applied_target["id"]
        invoice_id = before["id"]

        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"type": "materials_services"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        after = resp.get_json()
        assert after["type"] == "materials_services"
        assert after["refunds_invoice_id"] is None
        assert after["settled_via"] is None
        assert after["applied_to_invoice_id"] is None

        # Persisted, not just echoed back in the mutation response.
        refetched = avoir_client.get(_invoice_url(avoir_app._test_project_id, invoice_id), headers=_auth(admin_token))
        assert refetched.status_code == 200
        refetched_data = refetched.get_json()
        assert refetched_data["refunds_invoice_id"] is None
        assert refetched_data["settled_via"] is None
        assert refetched_data["applied_to_invoice_id"] is None

        # Cap freed: a second avoir return for the target's full amount now succeeds —
        # would 400 (AppliedExceedsTarget) had the stranded link still been counted.
        second = _create_return(
            avoir_client,
            avoir_app,
            admin_token,
            applied_to=applied_target["id"],
            settled_via="avoir",
            unit_price=-300.0,
        )
        assert second.status_code == 201, second.get_data(as_text=True)

    def test_patch_type_back_to_return_does_not_resurrect_cleared_links(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=300.0)
        created = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-300.0
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        invoice_id = created.get_json()["id"]

        away = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"type": "materials_services"},
            headers=_auth(admin_token),
        )
        assert away.status_code == 200, away.get_data(as_text=True)
        assert away.get_json()["settled_via"] is None
        assert away.get_json()["applied_to_invoice_id"] is None

        back = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, invoice_id),
            json={"type": "return"},
            headers=_auth(admin_token),
        )
        assert back.status_code == 200, back.get_data(as_text=True)
        after = back.get_json()
        assert after["type"] == "return"
        assert after["settled_via"] is None
        assert after["applied_to_invoice_id"] is None
        assert after["refunds_invoice_id"] is None

        # Not just the response — the DB row must not have resurrected the link either.
        refetched = avoir_client.get(_invoice_url(avoir_app._test_project_id, invoice_id), headers=_auth(admin_token))
        assert refetched.status_code == 200
        refetched_data = refetched.get_json()
        assert refetched_data["settled_via"] is None
        assert refetched_data["applied_to_invoice_id"] is None


# ---------------------------------------------------------------------------
# Mutation responses carry paid_with_returns (M2)
# ---------------------------------------------------------------------------


class TestMutationResponsesIncludePaidWithReturns:
    """M2 regression: create/update responses must enrich paid_with_returns exactly
    like list/GET, so the FE detail view (which replaces state from the mutation
    response) doesn't show stale "paid with avoir" info until a manual refetch."""

    def test_put_on_paying_invoice_returns_non_empty_paid_with_returns(self, avoir_client, avoir_app, admin_token):
        target = _create_target(avoir_client, avoir_app, admin_token, amount=500.0)
        applied = _create_return(
            avoir_client, avoir_app, admin_token, applied_to=target["id"], settled_via="avoir", unit_price=-200.0
        )
        assert applied.status_code == 201, applied.get_data(as_text=True)
        applied_data = applied.get_json()

        # Edit an unrelated field on the paying (target) invoice via PUT.
        resp = avoir_client.put(
            _invoice_url(avoir_app._test_project_id, target["id"]),
            json={"notes": "updated via PUT"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["notes"] == "updated via PUT"
        assert len(data["paid_with_returns"]) == 1
        assert data["paid_with_returns"][0]["invoice_number"] == applied_data["invoice_number"]
        assert data["paid_with_returns"][0]["total_amount"] == -200.0

    def test_post_create_response_includes_paid_with_returns_key(self, avoir_client, avoir_app, admin_token):
        """A freshly created invoice has no returns applied to it yet, but the key must
        be present (empty list) — proves the enrichment call runs on create too."""
        resp = _create(avoir_client, avoir_app, admin_token, invoice_type="materials_services", unit_price=250.0)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["paid_with_returns"] == []
