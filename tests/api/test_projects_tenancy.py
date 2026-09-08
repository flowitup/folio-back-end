"""API integration tests: cross-company project tenancy (Phase 1 tenancy fixes).

Covers the `project:create`-as-see-everything proxy removal:
  - `GET /projects` for a company admin lists only their own company's projects.
  - GET/PUT on another company's project → 403/404, even for a company admin.
  - A bare JWT `project:create` claim (legacy global role, no company relation)
    grants NEITHER read NOR mutate on someone else's project — the exact
    tenancy hole this phase closes.
  - `POST /projects` by a company admin (no `*:*`) → 201, `company_id` set,
    creator becomes a project member.
  - `POST /projects` company resolution: explicit body `company_id` must be a
    company the caller admins (else 403); no resolvable company → 400.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def tenancy_app():
    """Flask app wired with in-memory SQLite: two companies, one project each.

    Seed layout:
      - company_a: admin_a (company role "admin"), project_a (owner=admin_a)
      - company_b: admin_b (company role "admin"), project_b (owner=admin_b)
      - claim_holder: NO company relation anywhere, global role carries the
        legacy `project:create` JWT claim (regression guard for the removed
        proxy) plus `project:read` (so the outer decorator's baseline check
        still passes — the tenancy gate under test is can_read/mutate_project).
      - outsider: no company relation, no elevated claim — plain "member"
        global role (project:read, log_own_attendance only).
      - "manager" legacy per-project role seeded so POST /projects can assign
        the creator to it.
    """
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
    from config import TestingConfig
    from wiring import configure_container, get_container

    class TenancyTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(TenancyTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        create_perm = PermissionModel(name="project:create", resource="project", action="create")
        attendance_perm = PermissionModel(
            name="project:log_own_attendance", resource="project", action="log_own_attendance"
        )
        star_perm = PermissionModel(name="*:*", resource="*", action="*")
        manager_role = RoleModel(name="manager", description="Legacy per-project manager")
        manager_role.permissions.extend([read_perm, create_perm])

        # global role carrying the legacy project:create claim, WITHOUT any
        # company relation — this is exactly the "tenancy hole" JWT shape.
        claim_role = RoleModel(name="legacy_claim_holder", description="Legacy admin claim, no company")
        claim_role.permissions.extend([read_perm, create_perm])

        member_role = RoleModel(name="tn_member", description="Plain member")
        member_role.permissions.extend([read_perm, attendance_perm])

        # Legacy global `*:*` — Low(b): a platform admin's body company_id
        # skips the per-company admin_ids check entirely, so a nonexistent
        # id must still 400 rather than hit the FK constraint (500).
        platform_admin_role = RoleModel(name="tn_platform_admin", description="Legacy platform admin")
        platform_admin_role.permissions.extend([star_perm, read_perm, create_perm])

        db.session.add_all(
            [
                read_perm,
                create_perm,
                attendance_perm,
                star_perm,
                manager_role,
                claim_role,
                member_role,
                platform_admin_role,
            ]
        )
        db.session.flush()

        def user(email: str, role: RoleModel) -> UserModel:
            u = UserModel(email=email, password_hash=hasher.hash(PASSWORD), is_active=True)
            u.roles.append(role)
            return u

        admin_a = user("tn_admin_a@test.com", member_role)
        admin_b = user("tn_admin_b@test.com", member_role)
        claim_holder = user("tn_claim_holder@test.com", claim_role)
        outsider = user("tn_outsider@test.com", member_role)
        platform_admin = user("tn_platform_admin@test.com", platform_admin_role)
        db.session.add_all([admin_a, admin_b, claim_holder, outsider, platform_admin])
        db.session.flush()

        now = datetime.now(timezone.utc)
        company_a = CompanyModel(
            id=uuid4(),
            legal_name="Tenancy Co A",
            address="1 rue A",
            created_by=admin_a.id,
            created_at=now,
            updated_at=now,
        )
        company_b = CompanyModel(
            id=uuid4(),
            legal_name="Tenancy Co B",
            address="2 rue B",
            created_by=admin_b.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_a, company_b])
        db.session.flush()

        project_a = ProjectModel(name="Project A", owner_id=admin_a.id, company_id=company_a.id)
        project_b = ProjectModel(name="Project B", owner_id=admin_b.id, company_id=company_b.id)
        db.session.add_all([project_a, project_b])
        db.session.flush()

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=admin_a.id, company_id=company_a.id, role="admin", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=admin_b.id, company_id=company_b.id, role="admin", is_primary=True, attached_at=now
                ),
            ]
        )
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        company_repo = SqlAlchemyCompanyRepository(db.session)
        access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
        role_repo = SqlAlchemyRoleRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            project_membership_repo=membership_repo,
            role_repo=role_repo,
        )

        from app.api.v1.authz_context import get_reader_cache

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo
        _c.authz_reader = SqlAlchemyAuthzReader(db.session, cache_provider=get_reader_cache)

        test_app._admin_a_id = admin_a.id
        test_app._admin_b_id = admin_b.id
        test_app._claim_holder_id = claim_holder.id
        test_app._outsider_id = outsider.id
        test_app._platform_admin_id = platform_admin.id
        test_app._company_a_id = company_a.id
        test_app._company_b_id = company_b.id
        test_app._project_a_id = project_a.id
        test_app._project_b_id = project_b.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(tenancy_app):
    return tenancy_app.test_client()


def _login(client, email: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return {"Authorization": f"Bearer {resp.get_json()['access_token']}"}


@pytest.fixture
def admin_a_h(client):
    return _login(client, "tn_admin_a@test.com")


@pytest.fixture
def admin_b_h(client):
    return _login(client, "tn_admin_b@test.com")


@pytest.fixture
def claim_holder_h(client):
    return _login(client, "tn_claim_holder@test.com")


@pytest.fixture
def outsider_h(client):
    return _login(client, "tn_outsider@test.com")


@pytest.fixture
def platform_admin_h(client):
    return _login(client, "tn_platform_admin@test.com")


# ---------------------------------------------------------------------------
# GET /projects — company-scoped listing.
# ---------------------------------------------------------------------------


def test_list_projects_scoped_to_own_company(client, admin_a_h, tenancy_app):
    resp = client.get("/api/v1/projects", headers=admin_a_h)
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.get_json()["projects"]}
    assert str(tenancy_app._project_a_id) in ids
    assert str(tenancy_app._project_b_id) not in ids


def test_list_projects_items_include_company_id(client, admin_a_h, tenancy_app):
    """GET /projects list items carry company_id, same as the single GET /projects/<id>."""
    resp = client.get("/api/v1/projects", headers=admin_a_h)
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.get_json()["projects"]}
    project_a = by_id[str(tenancy_app._project_a_id)]
    assert project_a["company_id"] == str(tenancy_app._company_a_id)


def test_list_projects_claim_holder_sees_nothing_of_others(client, claim_holder_h, tenancy_app):
    """A bare global project:create claim, with no company/membership, sees no projects."""
    resp = client.get("/api/v1/projects", headers=claim_holder_h)
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.get_json()["projects"]}
    assert str(tenancy_app._project_a_id) not in ids
    assert str(tenancy_app._project_b_id) not in ids


# ---------------------------------------------------------------------------
# Cross-company read/mutate — 403/404 for a company admin on another tenant.
# ---------------------------------------------------------------------------


def test_admin_of_a_gets_denied_on_bs_project_get(client, admin_a_h, tenancy_app):
    resp = client.get(f"/api/v1/projects/{tenancy_app._project_b_id}", headers=admin_a_h)
    assert resp.status_code == 403


def test_admin_of_a_gets_denied_on_bs_project_put(client, admin_a_h, tenancy_app):
    resp = client.put(f"/api/v1/projects/{tenancy_app._project_b_id}", json={"name": "Hijacked"}, headers=admin_a_h)
    assert resp.status_code == 403


def test_admin_of_a_can_read_own_project(client, admin_a_h, tenancy_app):
    resp = client.get(f"/api/v1/projects/{tenancy_app._project_a_id}", headers=admin_a_h)
    assert resp.status_code == 200


def test_claim_holder_cannot_read_any_project(client, claim_holder_h, tenancy_app):
    """The removed tenancy hole: a raw JWT project:create claim must not grant read."""
    resp_a = client.get(f"/api/v1/projects/{tenancy_app._project_a_id}", headers=claim_holder_h)
    resp_b = client.get(f"/api/v1/projects/{tenancy_app._project_b_id}", headers=claim_holder_h)
    assert resp_a.status_code == 403
    assert resp_b.status_code == 403


def test_claim_holder_cannot_mutate_any_project(client, claim_holder_h, tenancy_app):
    resp = client.put(
        f"/api/v1/projects/{tenancy_app._project_a_id}", json={"name": "Hijacked"}, headers=claim_holder_h
    )
    assert resp.status_code == 403


def test_outsider_cannot_read_any_project(client, outsider_h, tenancy_app):
    resp = client.get(f"/api/v1/projects/{tenancy_app._project_a_id}", headers=outsider_h)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /projects — company resolution + creator membership.
# ---------------------------------------------------------------------------


def test_create_project_company_admin_no_star_gets_201_with_company_id(client, admin_a_h, tenancy_app):
    resp = client.post("/api/v1/projects", json={"name": "New A Project"}, headers=admin_a_h)
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["company_id"] == str(tenancy_app._company_a_id)
    # Low(a): the creator was actually assigned (legacy "manager" membership
    # row) — the response must reflect that as user_count=1, not the
    # hardcoded 0 that ignored whether the assignment succeeded.
    assert body["user_count"] == 1

    # Creator is a project member — verified against user_projects directly
    # via the authz reader (dialect/insert-path-safe; the /members route's raw
    # SQL JOIN between `users` (ORM-inserted) and `user_projects`
    # (raw-text-inserted) is a pre-existing SQLite-only landmine unrelated to
    # this phase — see SqlAlchemyAuthzReader's module docstring).
    from wiring import get_container

    with tenancy_app.app_context():
        reader = get_container().authz_reader
        assert reader.is_assigned(tenancy_app._admin_a_id, UUID(body["id"])) is True


def test_create_project_explicit_company_id_must_be_own(client, admin_a_h, tenancy_app):
    """Body company_id of a company the caller does NOT admin → 403."""
    resp = client.post(
        "/api/v1/projects",
        json={"name": "Hijack Attempt", "company_id": str(tenancy_app._company_b_id)},
        headers=admin_a_h,
    )
    assert resp.status_code == 403


def test_create_project_explicit_own_company_id_accepted(client, admin_a_h, tenancy_app):
    resp = client.post(
        "/api/v1/projects",
        json={"name": "Explicit Own Co", "company_id": str(tenancy_app._company_a_id)},
        headers=admin_a_h,
    )
    assert resp.status_code == 201
    assert resp.get_json()["company_id"] == str(tenancy_app._company_a_id)


def test_create_project_no_company_relation_gets_400(client, outsider_h):
    """No company admin-ship anywhere, no `*:*` → 400 (no company to attach to)."""
    resp = client.post("/api/v1/projects", json={"name": "Orphan Project"}, headers=outsider_h)
    assert resp.status_code == 400


def test_create_project_claim_holder_gets_400(client, claim_holder_h):
    """A bare JWT project:create claim is NOT a company — 400, not 201."""
    resp = client.post("/api/v1/projects", json={"name": "Should Not Exist"}, headers=claim_holder_h)
    assert resp.status_code == 400


def test_create_project_platform_admin_nonexistent_company_id_gets_400(client, platform_admin_h):
    """Low(b): a platform admin's body company_id skips the admin_ids check entirely —
    a bogus id must 400, not hit the projects.company_id FK constraint (500)."""
    resp = client.post(
        "/api/v1/projects",
        json={"name": "Bogus Company", "company_id": str(uuid4())},
        headers=platform_admin_h,
    )
    assert resp.status_code == 400


def test_create_project_platform_admin_existing_company_id_still_works(client, platform_admin_h, tenancy_app):
    """The 400 guard above must not regress the existing-company path."""
    resp = client.post(
        "/api/v1/projects",
        json={"name": "Platform Admin Project", "company_id": str(tenancy_app._company_a_id)},
        headers=platform_admin_h,
    )
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["company_id"] == str(tenancy_app._company_a_id)


# ---------------------------------------------------------------------------
# M1 — role change applies on the very next request, even though this
# fixture keeps one Flask app context open across every test-client call in
# this module (see app.api.v1.authz_context module docstring).
# ---------------------------------------------------------------------------


def test_role_downgrade_takes_effect_on_the_next_request(client, admin_a_h, tenancy_app):
    from app import db
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    # First request: admin_a is still company "admin" — full resolver rights
    # on their own project, including project:update.
    resp1 = client.get(f"/api/v1/projects/{tenancy_app._project_a_id}", headers=admin_a_h)
    assert resp1.status_code == 200
    assert "project:update" in resp1.get_json()["my_permissions"]

    # Demote admin_a to "member" directly in the DB (no re-login — the JWT
    # carries identity only, never the role).
    with tenancy_app.app_context():
        row = (
            db.session.query(UserCompanyAccessModel)
            .filter_by(user_id=tenancy_app._admin_a_id, company_id=tenancy_app._company_a_id)
            .one()
        )
        row.role = "member"
        db.session.commit()

    # Second request, same token: without clearing the per-request authz
    # memo at the start of every request, flask.g would still hold the
    # first request's cached resolver output (this fixture's outer
    # app_context is reused across test_client() calls) and this would
    # incorrectly still show project:update.
    resp2 = client.get(f"/api/v1/projects/{tenancy_app._project_a_id}", headers=admin_a_h)
    assert resp2.status_code == 200
    assert "project:update" not in resp2.get_json()["my_permissions"]

    # Restore state so later tests in this module see admin_a as admin again.
    with tenancy_app.app_context():
        row = (
            db.session.query(UserCompanyAccessModel)
            .filter_by(user_id=tenancy_app._admin_a_id, company_id=tenancy_app._company_a_id)
            .one()
        )
        row.role = "admin"
        db.session.commit()
