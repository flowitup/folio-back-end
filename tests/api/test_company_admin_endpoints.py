"""API + use-case tests: company admins manage their own company without platform '*:*'.

Phase 1 of the roles/permissions redesign moves company management
(members, invite tokens, join code, company profile, payment methods) from
"platform admin only" to "company admin of THIS company, or platform admin".

Covers, for every endpoint listed in the phase spec:
  - a company admin (no '*:*') succeeds on their own company;
  - the same company admin gets 403 on a *different* company;
  - a plain member (role='member') gets 403;
  - a platform admin ('*:*') still succeeds on any company.

Also includes one use-case-level test (bypassing the route/decorator layer)
proving the guard lives in the use case, not only in the HTTP decorator.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel


# ---------------------------------------------------------------------------
# App fixture — isolated Flask test app with in-memory SQLite.
#
# Unlike tests/api/test_payment_methods_api.py, this fixture relies entirely
# on create_app()'s own internal wiring (it already wires every companies +
# payment_methods use-case, including the is_company_admin() lookup injected
# in app/__init__.py) instead of re-calling configure_container() — a second
# configure_container() call would replace authorization_service with a fresh
# instance that never gets the company-role lookup injected.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cadm_app():
    """Flask app with two companies, each admin/member combination pre-seeded."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig

    class CadmTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CadmTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")

        platform_admin_role = RoleModel(name="cadm_platform_admin_role", description="Platform admin")
        platform_admin_role.permissions.append(star_perm)

        no_perm_role = RoleModel(name="cadm_no_perm_role", description="No global permission")
        no_perm_role.permissions.append(read_perm)

        db.session.add_all([star_perm, read_perm, platform_admin_role, no_perm_role])
        db.session.flush()

        def _user(email: str, role) -> UserModel:
            u = UserModel(email=email, password_hash=hasher.hash("Passw0rd!"), is_active=True)
            u.roles.append(role)
            db.session.add(u)
            return u

        platform_admin = _user("cadm_platform_admin@test.com", platform_admin_role)
        # Platform access is the ops flag now, not the legacy `*:*` role.
        platform_admin.is_platform_ops = True
        company_a_admin = _user("cadm_company_a_admin@test.com", no_perm_role)
        company_b_admin = _user("cadm_company_b_admin@test.com", no_perm_role)
        plain_member = _user("cadm_plain_member@test.com", no_perm_role)
        db.session.flush()

        now = datetime.now(timezone.utc)

        def _company(name: str) -> CompanyModel:
            c = CompanyModel(
                id=uuid4(),
                legal_name=name,
                address="1 rue de la Paix",
                created_by=platform_admin.id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(c)
            return c

        company_a = _company("Company A SARL")
        company_b = _company("Company B SARL")
        db.session.flush()

        def _access(user_id: UUID, company_id: UUID, role: str, is_primary: bool = True) -> None:
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user_id,
                    company_id=company_id,
                    role=role,
                    is_primary=is_primary,
                    attached_at=now,
                )
            )

        _access(company_a_admin.id, company_a.id, "admin")
        _access(company_b_admin.id, company_b.id, "admin")
        _access(plain_member.id, company_a.id, "member")

        db.session.commit()

        test_app._test_admin_email = "cadm_platform_admin@test.com"
        test_app._test_company_a_admin_email = "cadm_company_a_admin@test.com"
        test_app._test_company_b_admin_email = "cadm_company_b_admin@test.com"
        test_app._test_member_email = "cadm_plain_member@test.com"
        test_app._test_password = "Passw0rd!"
        test_app._test_company_a_id = str(company_a.id)
        test_app._test_company_b_id = str(company_b.id)
        test_app._test_member_user_id = str(plain_member.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def cadm_client(cadm_app):
    return cadm_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def platform_admin_token(cadm_client, cadm_app):
    return _login(cadm_client, cadm_app._test_admin_email, cadm_app._test_password)


@pytest.fixture
def company_a_admin_token(cadm_client, cadm_app):
    return _login(cadm_client, cadm_app._test_company_a_admin_email, cadm_app._test_password)


@pytest.fixture
def company_b_admin_token(cadm_client, cadm_app):
    return _login(cadm_client, cadm_app._test_company_b_admin_email, cadm_app._test_password)


@pytest.fixture
def member_token(cadm_client, cadm_app):
    return _login(cadm_client, cadm_app._test_member_email, cadm_app._test_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Update company
# ---------------------------------------------------------------------------


class TestUpdateCompany:
    def test_company_admin_updates_own_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.put(
            f"/api/v1/companies/{cadm_app._test_company_a_id}",
            json={"legal_name": "Company A Renamed"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["legal_name"] == "Company A Renamed"

    def test_company_admin_403_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.put(
            f"/api/v1/companies/{cadm_app._test_company_b_id}",
            json={"legal_name": "Hijacked"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.put(
            f"/api/v1/companies/{cadm_app._test_company_a_id}",
            json={"legal_name": "Nope"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_platform_admin_still_works(self, cadm_client, cadm_app, platform_admin_token):
        resp = cadm_client.put(
            f"/api/v1/companies/{cadm_app._test_company_b_id}",
            json={"legal_name": "Company B via platform admin"},
            headers=_auth(platform_admin_token),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# List attached users
# ---------------------------------------------------------------------------


class TestListAttachedUsers:
    def test_company_admin_lists_own_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.get(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/attached-users",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["total"] >= 2  # company_a_admin + plain_member
        # Rows carry a human label: clients render the member list from this payload alone.
        rows = resp.get_json()["items"]
        assert rows and all({"user_id", "role", "email", "display_name", "phone"} <= set(r) for r in rows)
        assert all(r["email"] and r["display_name"] for r in rows)
        # The OpenAPI spec declares the same shape so typed clients see the label fields.
        spec = cadm_client.get("/openapi.json").get_json()
        schema = spec["components"]["schemas"]["AttachedUserRow"]["properties"]
        assert {"email", "display_name", "phone"} <= set(schema)

    def test_company_admin_403_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.get(
            f"/api/v1/companies/{cadm_app._test_company_b_id}/attached-users",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.get(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/attached-users",
            headers=_auth(member_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Set member role
# ---------------------------------------------------------------------------


class TestSetMemberRole:
    def _make_target(self, cadm_app, company_id: str) -> str:
        """Attach a throwaway member to *company_id* to promote/demote in isolation."""
        from app import db

        with cadm_app.app_context():
            now = datetime.now(timezone.utc)
            target = UserModel(email=f"cadm_target_{uuid4().hex[:8]}@test.com", password_hash="x", is_active=True)
            db.session.add(target)
            db.session.flush()
            db.session.add(
                UserCompanyAccessModel(
                    user_id=target.id,
                    company_id=UUID(company_id),
                    role="member",
                    is_primary=True,
                    attached_at=now,
                )
            )
            db.session.commit()
            return str(target.id)

    def test_company_admin_changes_role_in_own_company(self, cadm_client, cadm_app, company_a_admin_token):
        target_id = self._make_target(cadm_app, cadm_app._test_company_a_id)
        resp = cadm_client.patch(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/access/{target_id}/role",
            json={"role": "admin"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["role"] == "admin"

    def test_company_admin_403_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        target_id = self._make_target(cadm_app, cadm_app._test_company_b_id)
        resp = cadm_client.patch(
            f"/api/v1/companies/{cadm_app._test_company_b_id}/access/{target_id}/role",
            json={"role": "admin"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403(self, cadm_client, cadm_app, member_token):
        target_id = self._make_target(cadm_app, cadm_app._test_company_a_id)
        resp = cadm_client.patch(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/access/{target_id}/role",
            json={"role": "admin"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Invite tokens
# ---------------------------------------------------------------------------


class TestInviteTokens:
    def test_company_admin_generates_and_revokes_own_company(self, cadm_client, cadm_app, company_a_admin_token):
        gen = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/invite-tokens",
            headers=_auth(company_a_admin_token),
        )
        assert gen.status_code == 201, gen.get_data(as_text=True)

        rev = cadm_client.delete(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/invite-tokens/active",
            headers=_auth(company_a_admin_token),
        )
        assert rev.status_code == 204

    def test_company_admin_403_generate_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_b_id}/invite-tokens",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403_generate(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/invite-tokens",
            headers=_auth(member_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Join code
# ---------------------------------------------------------------------------


class TestJoinCode:
    def test_company_admin_sets_and_revokes_own_company(self, cadm_client, cadm_app, company_a_admin_token):
        issued = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/join-code",
            headers=_auth(company_a_admin_token),
        )
        assert issued.status_code == 200, issued.get_data(as_text=True)
        assert issued.get_json()["join_code"]

        revoked = cadm_client.delete(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/join-code",
            headers=_auth(company_a_admin_token),
        )
        assert revoked.status_code == 204

    def test_company_admin_403_set_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_b_id}/join-code",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403_set(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.post(
            f"/api/v1/companies/{cadm_app._test_company_a_id}/join-code",
            headers=_auth(member_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Payment methods
# ---------------------------------------------------------------------------


class TestPaymentMethods:
    def _list_url(self, company_id: str) -> str:
        return f"/api/v1/companies/{company_id}/payment-methods"

    def test_company_admin_manages_own_company_payment_methods(self, cadm_client, cadm_app, company_a_admin_token):
        created = cadm_client.post(
            self._list_url(cadm_app._test_company_a_id),
            json={"label": "Cheque"},
            headers=_auth(company_a_admin_token),
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        method_id = created.get_json()["id"]

        updated = cadm_client.patch(
            f"{self._list_url(cadm_app._test_company_a_id)}/{method_id}",
            json={"label": "Cheque bancaire"},
            headers=_auth(company_a_admin_token),
        )
        assert updated.status_code == 200

        deleted = cadm_client.delete(
            f"{self._list_url(cadm_app._test_company_a_id)}/{method_id}",
            headers=_auth(company_a_admin_token),
        )
        assert deleted.status_code == 204

    def test_company_admin_403_create_on_other_company(self, cadm_client, cadm_app, company_a_admin_token):
        resp = cadm_client.post(
            self._list_url(cadm_app._test_company_b_id),
            json={"label": "Cheque"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 403

    def test_member_403_create(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.post(
            self._list_url(cadm_app._test_company_a_id),
            json={"label": "Cheque"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_company_admin_sees_inactive_rows(self, cadm_client, cadm_app, company_a_admin_token):
        """include_inactive=true is now company-admin-or-platform-admin, not platform-only."""
        from app import db

        with cadm_app.app_context():
            now = datetime.now(timezone.utc)
            row = PaymentMethodModel(
                id=uuid4(),
                company_id=UUID(cadm_app._test_company_a_id),
                label="Inactive Wire",
                is_builtin=False,
                is_active=False,
                created_by=UUID(cadm_app._test_member_user_id),
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
            db.session.commit()

        resp = cadm_client.get(
            self._list_url(cadm_app._test_company_a_id),
            query_string={"include_inactive": "true"},
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200
        labels = [i["label"] for i in resp.get_json()["items"]]
        assert "Inactive Wire" in labels

    def test_member_does_not_see_inactive_rows(self, cadm_client, cadm_app, member_token):
        resp = cadm_client.get(
            self._list_url(cadm_app._test_company_a_id),
            query_string={"include_inactive": "true"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        labels = [i["label"] for i in resp.get_json()["items"]]
        assert "Inactive Wire" not in labels


# ---------------------------------------------------------------------------
# Use-case-level guard (bypasses the HTTP decorator entirely)
# ---------------------------------------------------------------------------


class _LocalFakeRoleChecker:
    """Minimal RoleCheckerPort double: only company-admin, never platform admin."""

    def __init__(self, company_admins: set[tuple[UUID, UUID]]):
        self._company_admins = company_admins

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        return False

    def is_platform_admin(self, user_id: UUID) -> bool:
        return False

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        return (user_id, company_id) in self._company_admins


class _FakeSession:
    def commit(self) -> None:
        pass


class TestUseCaseLevelGuard:
    """Proves _assert_company_admin lives inside the use case, not only the route."""

    def test_update_company_usecase_accepts_company_admin_without_platform_permission(self):
        from app.application.companies.dtos import UpdateCompanyInput
        from app.application.companies.update_company_usecase import UpdateCompanyUseCase
        from app.domain.companies.company import Company

        caller_id = uuid4()
        company_id = uuid4()
        now = datetime.now(timezone.utc)
        company = Company(
            id=company_id,
            legal_name="Use Case Co",
            address="1 rue Test",
            siret=None,
            tva_number=None,
            iban=None,
            bic=None,
            logo_url=None,
            default_payment_terms=None,
            prefix_override=None,
            created_by=caller_id,
            created_at=now,
            updated_at=now,
        )

        class _Repo:
            def find_by_id(self, cid):
                return company if cid == company_id else None

            def save(self, c):
                return c

        role_checker = _LocalFakeRoleChecker({(caller_id, company_id)})
        usecase = UpdateCompanyUseCase(company_repo=_Repo(), role_checker=role_checker)

        result = usecase.execute(
            UpdateCompanyInput(id=company_id, caller_id=caller_id, legal_name="Renamed via use case"),
            _FakeSession(),
        )
        assert result.legal_name == "Renamed via use case"

    def test_update_company_usecase_rejects_admin_of_different_company(self):
        from app.application.companies.dtos import UpdateCompanyInput
        from app.application.companies.update_company_usecase import UpdateCompanyUseCase
        from app.domain.companies.company import Company
        from app.domain.companies.exceptions import ForbiddenCompanyError

        caller_id = uuid4()
        company_id = uuid4()
        other_company_id = uuid4()
        now = datetime.now(timezone.utc)
        company = Company(
            id=company_id,
            legal_name="Use Case Co",
            address="1 rue Test",
            siret=None,
            tva_number=None,
            iban=None,
            bic=None,
            logo_url=None,
            default_payment_terms=None,
            prefix_override=None,
            created_by=caller_id,
            created_at=now,
            updated_at=now,
        )

        class _Repo:
            def find_by_id(self, cid):
                return company if cid == company_id else None

        # Admin of other_company_id only — not of company_id.
        role_checker = _LocalFakeRoleChecker({(caller_id, other_company_id)})
        usecase = UpdateCompanyUseCase(company_repo=_Repo(), role_checker=role_checker)

        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(
                UpdateCompanyInput(id=company_id, caller_id=caller_id, legal_name="Should not apply"),
                _FakeSession(),
            )
