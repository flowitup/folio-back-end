"""API integration tests: bank-refund funds release reconciliation.

Covers PATCH /api/v1/billing/materials-expenses/<invoice_id> triggering the
automatic released_funds release described in phase 03, end to end through
the real HTTP layer, real SetInvoiceRefundableStatusUseCase, and a real
FundsReleaseAdapter/SQLAlchemyInvoiceRepository backed by SQLite.

Aggregate non-regression (sum_company_spent, sum_funds_released) is asserted
directly against SQLAlchemyInvoiceRepository rather than through the
projects-list HTTP API, since the projects "spent" endpoint is outside this
feature's owned files.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app import db
from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(
    project_id: UUID,
    user_id: UUID,
    invoice_type: str = "materials_services",
    refundable_status: str | None = None,
    refunded_by: str | None = None,
    quantity: float = 1.0,
    unit_price: float = 500.0,
    issue_date: date | None = None,
) -> InvoiceModel:
    now = datetime.now(timezone.utc)
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=f"INV-{uuid4().hex[:8]}",
        type=invoice_type,
        issue_date=issue_date or date.today(),
        recipient_name="Test Supplier",
        items=[{"description": "Materials", "quantity": quantity, "unit_price": unit_price, "vat_rate": 0}],
        created_by=user_id,
        created_at=now,
        updated_at=now,
        refundable_status=refundable_status,
        refunded_by=refunded_by,
    )
    db.session.add(inv)
    db.session.commit()
    return inv


def _find_release(project_id: UUID, source_id: UUID) -> InvoiceModel | None:
    return (
        db.session.query(InvoiceModel)
        .filter(
            InvoiceModel.project_id == project_id,
            InvoiceModel.type == "released_funds",
            InvoiceModel.refunds_invoice_id == source_id,
        )
        .first()
    )


@pytest.fixture(scope="module")
def bank_refund_app():
    """Flask app wired the same way as production: SetInvoiceRefundableStatusUseCase
    receives a real FundsReleaseAdapter, so PATCH requests reconcile for real."""
    from app import create_app
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.funds_release_adapter import FundsReleaseAdapter
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.application.invoice.list_materials_expenses_usecase import ListMaterialsExpensesUseCase
    from app.application.invoice.set_refundable_status_usecase import SetInvoiceRefundableStatusUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class BankRefundTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(BankRefundTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        superadmin_role = RoleModel(name="bank_refund_superadmin", description="Superadmin")
        superadmin_role.permissions.append(star_perm)
        db.session.add_all([star_perm, superadmin_role])
        db.session.flush()

        admin_user = UserModel(
            email="bank_refund_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(superadmin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="Bank Refund Test Co",
            address="1 rue de la Paix",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(name="Bank Refund Project", owner_id=admin_user.id, company_id=company.id)
        db.session.add(project)
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)
        company_repo = SqlAlchemyCompanyRepository(db.session)
        access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo

        funds_release_adapter = FundsReleaseAdapter(invoice_repo=invoice_repo)

        _c.list_materials_expenses_usecase = ListMaterialsExpensesUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
        )
        _c.set_refundable_status_usecase = SetInvoiceRefundableStatusUseCase(
            invoice_repo=invoice_repo,
            access_repo=access_repo,
            funds_release=funds_release_adapter,
        )

        test_app._admin_user_id = admin_user.id
        test_app._project_id = project.id
        test_app._invoice_repo = invoice_repo

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def brl_client(bank_refund_app):
    return bank_refund_app.test_client()


@pytest.fixture(scope="module")
def admin_tok(brl_client):
    resp = brl_client.post(
        "/api/v1/auth/login",
        json={"email": "bank_refund_admin@test.com", "password": "Admin1234!"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


def _patch(client, tok, invoice_id, refundable_status, refunded_by=None):
    body = {"refundable_status": refundable_status}
    if refunded_by is not None:
        body["refunded_by"] = refunded_by
    return client.patch(
        f"/api/v1/billing/materials-expenses/{invoice_id}",
        json=body,
        headers=_auth(tok),
    )


class TestReleaseCreatedOnBankOrBoth:
    @pytest.mark.parametrize("refunded_by", ["bank", "both"])
    def test_patch_refunded_bank_or_both_creates_release(self, brl_client, admin_tok, bank_refund_app, refunded_by):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=500.0)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", refunded_by)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            release = _find_release(project_id, inv_id)
            assert release is not None
            assert release.is_auto_generated is True

    def test_patch_refunded_company_creates_no_release(self, brl_client, admin_tok, bank_refund_app):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "company")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is None

    def test_patch_refunded_without_refunded_by_creates_no_release(self, brl_client, admin_tok, bank_refund_app):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["refunded_by"] == "company"

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is None


class TestReleaseDeletedOnTransitionsAway:
    def _seed_bank_refunded(self, bank_refund_app, brl_client, admin_tok):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=750.0)
            inv_id, project_id = inv.id, bank_refund_app._project_id
        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "bank")
        assert resp.status_code == 200
        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is not None
        return inv_id, project_id

    def test_bank_to_company_deletes_release(self, brl_client, admin_tok, bank_refund_app):
        inv_id, project_id = self._seed_bank_refunded(bank_refund_app, brl_client, admin_tok)

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "company")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is None

    def test_bank_to_refund_pending_deletes_release(self, brl_client, admin_tok, bank_refund_app):
        inv_id, project_id = self._seed_bank_refunded(bank_refund_app, brl_client, admin_tok)

        resp = _patch(brl_client, admin_tok, inv_id, "refund_pending")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is None

    def test_bank_to_null_deletes_release(self, brl_client, admin_tok, bank_refund_app):
        inv_id, project_id = self._seed_bank_refunded(bank_refund_app, brl_client, admin_tok)

        resp = _patch(brl_client, admin_tok, inv_id, None)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is None

    def test_bank_to_bank_repeat_still_exactly_one_release(self, brl_client, admin_tok, bank_refund_app):
        inv_id, project_id = self._seed_bank_refunded(bank_refund_app, brl_client, admin_tok)

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "bank")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            rows = (
                db.session.query(InvoiceModel)
                .filter(InvoiceModel.refunds_invoice_id == inv_id, InvoiceModel.type == "released_funds")
                .all()
            )
            assert len(rows) == 1


class TestHasBankRefundUnaffected:
    def test_has_bank_refund_unaffected_by_release_creation(self, brl_client, admin_tok, bank_refund_app):
        """has_bank_refund keys off type='refund' linkage only — creating a
        released_funds row for the same source must never flip it."""
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=300.0)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "bank")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            assert _find_release(project_id, inv_id) is not None

        list_resp = brl_client.get(
            f"/api/v1/billing/materials-expenses?refundable=true&company_id={_company_id(bank_refund_app)}",
            headers=_auth(admin_tok),
        )
        assert list_resp.status_code == 200, list_resp.get_data(as_text=True)
        items = {i["id"]: i for i in list_resp.get_json()["items"]}
        assert str(inv_id) in items
        assert items[str(inv_id)]["has_bank_refund"] is False  # no type='refund' row exists for this source


class TestFundsReleaseNumberField:
    """funds_release_number surfaces the linked bank-refund release's FR number."""

    def test_bank_refunded_row_returns_its_release_number(self, brl_client, admin_tok, bank_refund_app):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=350.0)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "bank")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with bank_refund_app.app_context():
            release = _find_release(project_id, inv_id)
            assert release is not None
            expected_number = release.invoice_number

        list_resp = brl_client.get(
            f"/api/v1/billing/materials-expenses?refundable=true&company_id={_company_id(bank_refund_app)}",
            headers=_auth(admin_tok),
        )
        assert list_resp.status_code == 200, list_resp.get_data(as_text=True)
        items = {i["id"]: i for i in list_resp.get_json()["items"]}
        assert items[str(inv_id)]["funds_release_number"] == expected_number

    def test_company_refunded_row_returns_null_funds_release_number(self, brl_client, admin_tok, bank_refund_app):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=180.0)
            inv_id = inv.id

        resp = _patch(brl_client, admin_tok, inv_id, "refunded", "company")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        list_resp = brl_client.get(
            f"/api/v1/billing/materials-expenses?refundable=true&company_id={_company_id(bank_refund_app)}",
            headers=_auth(admin_tok),
        )
        assert list_resp.status_code == 200, list_resp.get_data(as_text=True)
        items = {i["id"]: i for i in list_resp.get_json()["items"]}
        assert items[str(inv_id)]["funds_release_number"] is None

    def test_empty_source_ids_returns_empty_dict_without_querying(self, bank_refund_app, monkeypatch):
        """Empty input short-circuits before touching the session — never emits an IN ()."""
        with bank_refund_app.app_context():
            repo: SQLAlchemyInvoiceRepository = bank_refund_app._invoice_repo

            def _fail_if_called(*args, **kwargs):
                raise AssertionError("query() must not be called for empty source_ids")

            monkeypatch.setattr(repo._session, "query", _fail_if_called)

            assert repo.bank_refund_release_numbers([]) == {}


def _company_id(bank_refund_app) -> str:
    with bank_refund_app.app_context():
        row = db.session.query(ProjectModel.company_id).filter_by(id=bank_refund_app._project_id).first()
        return str(row[0])


class TestReconcileFailureRollsBackStatus:
    def test_reconcile_raise_leaves_status_unchanged_and_no_release(self, brl_client, admin_tok, bank_refund_app):
        with bank_refund_app.app_context():
            inv = _make_invoice(bank_refund_app._project_id, bank_refund_app._admin_user_id, unit_price=444.0)
            inv_id, project_id = inv.id, bank_refund_app._project_id

        from wiring import get_container

        with bank_refund_app.app_context():
            uc = get_container().set_refundable_status_usecase
            original_funds_release = uc._funds_release
            uc._funds_release = _RaisingFundsRelease()

        original_propagate = bank_refund_app.config.get("PROPAGATE_EXCEPTIONS")
        bank_refund_app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            resp = _patch(brl_client, admin_tok, inv_id, "refunded", "bank")
            assert resp.status_code == 500
        finally:
            bank_refund_app.config["PROPAGATE_EXCEPTIONS"] = original_propagate
            with bank_refund_app.app_context():
                get_container().set_refundable_status_usecase._funds_release = original_funds_release

        with bank_refund_app.app_context():
            row = db.session.query(InvoiceModel).filter_by(id=inv_id).first()
            assert row.refundable_status is None  # never persisted
            assert _find_release(project_id, inv_id) is None


class _RaisingFundsRelease:
    def create_bank_refund_release(self, source, created_by):
        raise RuntimeError("simulated reconcile failure")

    def delete_bank_refund_release(self, source_id):
        raise RuntimeError("simulated reconcile failure")


class TestAggregateNonRegression:
    def test_sum_company_spent_and_sum_funds_released(self, bank_refund_app):
        """sum_company_spent must be unchanged by marking bank-refunded;
        sum_funds_released must increase by exactly the source total."""
        with bank_refund_app.app_context():
            repo: SQLAlchemyInvoiceRepository = bank_refund_app._invoice_repo
            project_id = bank_refund_app._project_id

            before_company_spent = repo.sum_company_spent(project_id)
            before_funds_released = repo.sum_funds_released(project_id)

            inv = _make_invoice(project_id, bank_refund_app._admin_user_id, unit_price=650.0)
            inv_id = inv.id  # capture the scalar now — `inv` itself would be
            # DetachedInstanceError-prone once this app_context (and its
            # scoped db.session) tears down at the end of this `with` block.
            source_total = Decimal("650")

        from app.infrastructure.adapters.funds_release_adapter import FundsReleaseAdapter

        with bank_refund_app.app_context():
            adapter = FundsReleaseAdapter(invoice_repo=repo)
            source_entity = repo.find_by_id(inv_id)
            updated = source_entity.with_updates(refundable_status="refunded", refunded_by="bank")
            adapter.create_bank_refund_release(updated, created_by=bank_refund_app._admin_user_id)
            repo.update(updated)

            after_company_spent = repo.sum_company_spent(project_id)
            after_funds_released = repo.sum_funds_released(project_id)

        assert after_company_spent == before_company_spent  # bank refund is not company spend
        assert after_funds_released == before_funds_released + source_total
