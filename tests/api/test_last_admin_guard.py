"""No path may leave a company with zero admins.

Covers, at the route level, all three ways a company could lose its last admin:
  - PATCH .../access/<uid>/role   (demote the last admin to any other role)
  - DELETE .../access/<uid>       (boot the last admin)
  - DELETE .../access             (self-detach as the last admin)

Each is rejected with 409 when it is the company's only admin, and succeeds
when a second admin exists (the operation then leaves >=1 admin behind).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel


@pytest.fixture(scope="module")
def lag_app():
    """Flask app with a platform-admin login and a helper to seed companies."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig

    class LagTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(LagTestConfig)

    with test_app.app_context():
        db.create_all()
        hasher = Argon2PasswordHasher()
        no_perm_role = RoleModel(name="lag_no_perm_role", description="No global permission")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        no_perm_role.permissions.append(read_perm)
        db.session.add_all([read_perm, no_perm_role])
        db.session.flush()
        test_app._no_perm_role_id = no_perm_role.id
        test_app._password_hash = hasher.hash("Passw0rd!")
        db.session.commit()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def lag_client(lag_app):
    return lag_app.test_client()


def _login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "Passw0rd!"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_company_with_admins(lag_app, *, admin_count: int):
    """Create a fresh company with *admin_count* admins (role='admin').

    Returns (company_id: str, admin_emails: list[str], admin_ids: list[str]).
    The first admin is the one every test acts as/on; extra admins (if any)
    exist purely to satisfy the "at least one other admin" happy path.
    """
    from app import db

    with lag_app.app_context():
        now = datetime.now(timezone.utc)
        role = db.session.get(RoleModel, lag_app._no_perm_role_id)

        company = CompanyModel(
            id=uuid4(),
            legal_name=f"LastAdmin Co {uuid4().hex[:6]}",
            address="1 rue de la Paix",
            created_by=uuid4(),
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        admin_emails: list[str] = []
        admin_ids: list[str] = []
        for i in range(admin_count):
            email = f"lag_admin_{uuid4().hex[:10]}@test.com"
            user = UserModel(email=email, password_hash=lag_app._password_hash, is_active=True)
            user.roles.append(role)
            db.session.add(user)
            db.session.flush()
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user.id,
                    company_id=company.id,
                    role="admin",
                    is_primary=True,
                    attached_at=now,
                )
            )
            admin_emails.append(email)
            admin_ids.append(str(user.id))

        db.session.commit()
        return str(company.id), admin_emails, admin_ids


# ---------------------------------------------------------------------------
# Demote (set_member_role)
# ---------------------------------------------------------------------------


class TestDemoteLastAdmin:
    def test_demoting_the_only_admin_is_rejected(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=1)
        token = _login(lag_client, emails[0])

        resp = lag_client.patch(
            f"/api/v1/companies/{company_id}/access/{ids[0]}/role",
            json={"role": "member"},
            headers=_auth(token),
        )
        assert resp.status_code == 409
        assert resp.get_json()["reason"] == "last_admin"

    def test_demoting_one_of_two_admins_succeeds(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=2)
        token = _login(lag_client, emails[0])

        resp = lag_client.patch(
            f"/api/v1/companies/{company_id}/access/{ids[1]}/role",
            json={"role": "member"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["role"] == "member"


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------


class TestBootLastAdmin:
    def test_booting_the_only_admin_is_rejected(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=1)
        token = _login(lag_client, emails[0])

        resp = lag_client.delete(
            f"/api/v1/companies/{company_id}/access/{ids[0]}",
            headers=_auth(token),
        )
        assert resp.status_code == 409
        assert resp.get_json()["reason"] == "last_admin"

    def test_booting_one_of_two_admins_succeeds(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=2)
        token = _login(lag_client, emails[0])

        resp = lag_client.delete(
            f"/api/v1/companies/{company_id}/access/{ids[1]}",
            headers=_auth(token),
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Self-detach
# ---------------------------------------------------------------------------


class TestSelfDetachLastAdmin:
    def test_self_detaching_the_only_admin_is_rejected(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=1)
        token = _login(lag_client, emails[0])

        resp = lag_client.delete(
            f"/api/v1/companies/{company_id}/access",
            headers=_auth(token),
        )
        assert resp.status_code == 409
        assert resp.get_json()["reason"] == "last_admin"

    def test_self_detaching_one_of_two_admins_succeeds(self, lag_client, lag_app):
        company_id, emails, ids = _make_company_with_admins(lag_app, admin_count=2)
        token = _login(lag_client, emails[0])

        resp = lag_client.delete(
            f"/api/v1/companies/{company_id}/access",
            headers=_auth(token),
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Use-case-level: all three guards raise LastCompanyAdminError directly.
# ---------------------------------------------------------------------------


class _FakeSession:
    def commit(self) -> None:
        pass


class _FakeRoleChecker:
    """Always authorizes — these tests target the last-admin guard, not authz."""

    def has_permission(self, user_id, permission):
        return True

    def is_platform_admin(self, user_id):
        return True

    def is_company_admin(self, user_id, company_id):
        return True


class _InMemoryAccessRepo:
    """Minimal UserCompanyAccessRepositoryPort fake for use-case-level tests."""

    def __init__(self, rows):
        from app.domain.companies.user_company_access import UserCompanyAccess

        self._rows: dict[tuple[UUID, UUID], "UserCompanyAccess"] = {(r.user_id, r.company_id): r for r in rows}

    def find(self, user_id, company_id):
        return self._rows.get((user_id, company_id))

    def find_for_update(self, user_id, company_id):
        return self.find(user_id, company_id)

    def list_for_user(self, user_id):
        return [r for (uid, _), r in self._rows.items() if uid == user_id]

    def list_for_company(self, company_id):
        return [r for (_, cid), r in self._rows.items() if cid == company_id]

    def list_admins_for_update(self, company_id):
        return [r for r in self.list_for_company(company_id) if r.role == "admin"]

    def save(self, access):
        self._rows[(access.user_id, access.company_id)] = access
        return access

    def delete(self, user_id, company_id):
        self._rows.pop((user_id, company_id), None)

    def clear_primary_for_user(self, user_id):
        pass


def _access_row(user_id, company_id, role):
    from app.domain.companies.user_company_access import UserCompanyAccess

    return UserCompanyAccess(
        user_id=user_id,
        company_id=company_id,
        is_primary=True,
        attached_at=datetime.now(timezone.utc),
        role=role,
    )


class TestUseCaseLevelLastAdminGuard:
    def test_set_member_role_usecase_rejects_demoting_last_admin(self):
        from app.application.companies.dtos import SetMemberRoleInput
        from app.application.companies.set_member_role_usecase import SetMemberRoleUseCase
        from app.domain.companies.exceptions import LastCompanyAdminError

        company_id, admin_id = uuid4(), uuid4()
        repo = _InMemoryAccessRepo([_access_row(admin_id, company_id, "admin")])
        usecase = SetMemberRoleUseCase(access_repo=repo, role_checker=_FakeRoleChecker())

        with pytest.raises(LastCompanyAdminError):
            usecase.execute(
                SetMemberRoleInput(caller_id=admin_id, company_id=company_id, user_id=admin_id, role="member"),
                _FakeSession(),
            )

    def test_boot_attached_user_usecase_rejects_booting_last_admin(self):
        from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase
        from app.application.companies.dtos import BootAttachedUserInput
        from app.domain.companies.company import Company
        from app.domain.companies.exceptions import LastCompanyAdminError

        company_id, admin_id, caller_id = uuid4(), uuid4(), uuid4()
        repo = _InMemoryAccessRepo([_access_row(admin_id, company_id, "admin")])
        now = datetime.now(timezone.utc)
        company = Company(
            id=company_id,
            legal_name="Co",
            address="Addr",
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

        class _CompanyRepo:
            def find_by_id(self, cid):
                return company if cid == company_id else None

        usecase = BootAttachedUserUseCase(
            company_repo=_CompanyRepo(), access_repo=repo, role_checker=_FakeRoleChecker()
        )

        with pytest.raises(LastCompanyAdminError):
            usecase.execute(
                BootAttachedUserInput(caller_id=caller_id, company_id=company_id, target_user_id=admin_id),
                _FakeSession(),
            )

    def test_detach_company_usecase_rejects_self_detaching_last_admin(self):
        from app.application.companies.detach_company_usecase import DetachCompanyUseCase
        from app.application.companies.dtos import DetachCompanyInput
        from app.domain.companies.exceptions import LastCompanyAdminError

        company_id, admin_id = uuid4(), uuid4()
        repo = _InMemoryAccessRepo([_access_row(admin_id, company_id, "admin")])
        usecase = DetachCompanyUseCase(access_repo=repo)

        with pytest.raises(LastCompanyAdminError):
            usecase.execute(DetachCompanyInput(user_id=admin_id, company_id=company_id), _FakeSession())

    def test_detach_company_usecase_allows_self_detach_with_second_admin(self):
        from app.application.companies.detach_company_usecase import DetachCompanyUseCase
        from app.application.companies.dtos import DetachCompanyInput

        company_id, admin_id, other_admin_id = uuid4(), uuid4(), uuid4()
        repo = _InMemoryAccessRepo(
            [
                _access_row(admin_id, company_id, "admin"),
                _access_row(other_admin_id, company_id, "admin"),
            ]
        )
        usecase = DetachCompanyUseCase(access_repo=repo)

        usecase.execute(DetachCompanyInput(user_id=admin_id, company_id=company_id), _FakeSession())
        assert repo.find(admin_id, company_id) is None
