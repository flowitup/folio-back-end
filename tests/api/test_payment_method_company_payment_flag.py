"""API integration tests for the is_company_payment flag on payment methods.

Covers:
- PATCH /companies/<id>/payment-methods/<id> with is_company_payment=true toggles the flag
- PATCH with is_company_payment=false toggles it back
- is_company_payment appears in GET list and PATCH response
- Toggling works on builtin methods (no 409)
- PATCH with only is_company_payment (no label/is_active) is accepted
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cpf_app():
    """Flask app wired for company-payment-flag toggle tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
        SqlAlchemyPaymentMethodRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.application.payment_methods.list_payment_methods_usecase import ListPaymentMethodsUseCase
    from app.application.payment_methods.create_payment_method_usecase import CreatePaymentMethodUseCase
    from app.application.payment_methods.update_payment_method_usecase import UpdatePaymentMethodUseCase
    from app.application.payment_methods.delete_payment_method_usecase import DeletePaymentMethodUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class CpfTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CpfTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")

        admin_role = RoleModel(name="cpf_admin_role", description="Admin")
        admin_role.permissions.append(star_perm)
        admin_role.permissions.append(read_perm)

        db.session.add_all([star_perm, read_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="cpf_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        now = datetime.now(timezone.utc)
        company = CompanyModel(
            id=uuid4(),
            legal_name="CPF Corp SARL",
            address="1 rue",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
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
        _company_repo = SqlAlchemyCompanyRepository(db.session)
        _access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        _role_checker = _c.authorization_service

        _c.payment_method_repo = _pm_repo
        _c.company_repo = _company_repo
        _c.user_company_access_repo = _access_repo

        _c.list_payment_methods_usecase = ListPaymentMethodsUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
            access_repo=_access_repo,
            company_repo=_company_repo,
        )
        _c.create_payment_method_usecase = CreatePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.update_payment_method_usecase = UpdatePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )
        _c.delete_payment_method_usecase = DeletePaymentMethodUseCase(
            payment_method_repo=_pm_repo,
            role_checker=_role_checker,
        )

        test_app._test_admin_email = "cpf_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_company_id = str(company.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def cpf_client(cpf_app):
    return cpf_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(cpf_client, cpf_app):
    return _login(cpf_client, cpf_app._test_admin_email, cpf_app._test_admin_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _detail_url(company_id, pm_id):
    return f"/api/v1/companies/{company_id}/payment-methods/{pm_id}"


def _list_url(company_id):
    return f"/api/v1/companies/{company_id}/payment-methods"


def _insert_pm(app, *, is_company_payment: bool = False, is_builtin: bool = False) -> str:
    """Insert a PaymentMethodModel row directly; return string UUID."""
    from app import db

    with app.app_context():
        now = datetime.now(timezone.utc)
        row = PaymentMethodModel(
            id=uuid4(),
            company_id=UUID(app._test_company_id),
            label=f"PM-{uuid4().hex[:6]}",
            is_builtin=is_builtin,
            is_active=True,
            is_company_payment=is_company_payment,
            created_by=None,
            created_at=now,
            updated_at=now,
        )
        db.session.add(row)
        db.session.commit()
        return str(row.id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsCompanyPaymentToggle:
    def test_patch_sets_flag_true(self, cpf_client, cpf_app, admin_token):
        """PATCH is_company_payment=true stores the flag and returns it in the response."""
        pm_id = _insert_pm(cpf_app, is_company_payment=False)

        resp = cpf_client.patch(
            _detail_url(cpf_app._test_company_id, pm_id),
            json={"is_company_payment": True},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert "is_company_payment" in data
        assert data["is_company_payment"] is True

    def test_patch_clears_flag_to_false(self, cpf_client, cpf_app, admin_token):
        """PATCH is_company_payment=false clears the flag."""
        pm_id = _insert_pm(cpf_app, is_company_payment=True)

        resp = cpf_client.patch(
            _detail_url(cpf_app._test_company_id, pm_id),
            json={"is_company_payment": False},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["is_company_payment"] is False

    def test_flag_persisted_after_toggle(self, cpf_client, cpf_app, admin_token):
        """Flag persists: list response reflects the toggled value."""
        pm_id = _insert_pm(cpf_app, is_company_payment=False)

        # Toggle on
        cpf_client.patch(
            _detail_url(cpf_app._test_company_id, pm_id),
            json={"is_company_payment": True},
            headers=_auth(admin_token),
        )

        list_resp = cpf_client.get(_list_url(cpf_app._test_company_id), headers=_auth(admin_token))
        items = list_resp.get_json()["items"]
        pm_in_list = next((i for i in items if i["id"] == pm_id), None)
        assert pm_in_list is not None
        assert pm_in_list["is_company_payment"] is True

    def test_toggle_allowed_on_builtin(self, cpf_client, cpf_app, admin_token):
        """Toggling is_company_payment on a builtin method does not raise 409."""
        pm_id = _insert_pm(cpf_app, is_company_payment=False, is_builtin=True)

        resp = cpf_client.patch(
            _detail_url(cpf_app._test_company_id, pm_id),
            json={"is_company_payment": True},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["is_company_payment"] is True

    def test_patch_only_flag_accepted(self, cpf_client, cpf_app, admin_token):
        """PATCH body with only is_company_payment (no label/is_active) returns 200."""
        pm_id = _insert_pm(cpf_app, is_company_payment=False)

        resp = cpf_client.patch(
            _detail_url(cpf_app._test_company_id, pm_id),
            json={"is_company_payment": True},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200

    def test_list_response_includes_flag(self, cpf_client, cpf_app, admin_token):
        """GET list response includes is_company_payment on every item."""
        _insert_pm(cpf_app, is_company_payment=True)

        resp = cpf_client.get(_list_url(cpf_app._test_company_id), headers=_auth(admin_token))
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) > 0
        for item in items:
            assert "is_company_payment" in item
