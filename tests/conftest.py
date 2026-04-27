"""Pytest configuration and fixtures for database tests."""

from __future__ import annotations

import os
import sys
from typing import Optional
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure EMAIL_PROVIDER=inmemory and required vars are set before any app import
os.environ.setdefault("EMAIL_PROVIDER", "inmemory")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("APP_BASE_URL", "http://localhost:3000")

# Add project root to Python path for wiring module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.database.models import Base


# ---------------------------------------------------------------------------
# Low-level SQLAlchemy session fixtures (kept for unit/repository tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_url():
    """Get test database URL from environment or use default SQLite."""
    return os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture(scope="session")
def engine(test_db_url):
    """Create SQLAlchemy engine for tests."""
    return create_engine(test_db_url, echo=False)


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables for testing."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def session(engine, tables):
    """Create a new database session for a test."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# InMemoryEmailAdapter fixture — for assertions in integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def inmemory_email_adapter():
    """Return the global InMemoryEmailAdapter singleton and clear it before each test."""
    import wiring
    # Ensure the singleton is initialised (may be None if configure_container never ran)
    if wiring._inmemory_email_adapter is None:
        from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter
        wiring._inmemory_email_adapter = InMemoryEmailAdapter()
    adapter = wiring._inmemory_email_adapter
    adapter.clear()
    yield adapter
    adapter.clear()


# ---------------------------------------------------------------------------
# Flask app + test client fixtures for API-level tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def invitation_app():
    """Flask app wired with in-memory DB + InMemoryEmailAdapter for invitation tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_invitation import SqlAlchemyInvitationRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import SqlAlchemyProjectMembershipRepository
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.models import UserModel, RoleModel, PermissionModel, ProjectModel
    from config import TestingConfig
    from wiring import configure_container
    import wiring as _wiring
    from app.infrastructure.email.inmemory_adapter import InMemoryEmailAdapter
    from uuid import uuid4

    class InviteTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(InviteTestConfig)

    with test_app.app_context():
        db.create_all()

        # Ensure InMemoryEmailAdapter singleton is ready
        if _wiring._inmemory_email_adapter is None:
            _wiring._inmemory_email_adapter = InMemoryEmailAdapter()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        inv_repo = SqlAlchemyInvitationRepository(db.session)
        membership_repo = SqlAlchemyProjectMembershipRepository(db.session)
        role_repo = SqlAlchemyRoleRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=token_issuer,
            session_manager=FlaskSessionManager(),
            invitation_repo=inv_repo,
            project_membership_repo=membership_repo,
            role_repo=role_repo,
        )

        # ------------------------------------------------------------------
        # Seed roles + permissions (SQLite in-memory: no migration fixtures)
        # ------------------------------------------------------------------
        invite_perm = PermissionModel(name="project:invite", resource="project", action="invite")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="admin", description="Admin")
        member_role = RoleModel(name="member", description="Member")
        superadmin_role = RoleModel(name="superadmin", description="Superadmin")

        admin_role.permissions.append(invite_perm)
        admin_role.permissions.append(read_perm)
        member_role.permissions.append(read_perm)
        superadmin_role.permissions.append(star_perm)

        # Seed roles + permissions first so they get IDs before users reference them
        db.session.add_all([
            invite_perm, read_perm, star_perm,
            admin_role, member_role, superadmin_role,
        ])
        db.session.flush()  # assign DB-generated UUIDs

        # Seed users
        admin_user = UserModel(
            email="admin@invite-test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        member_user = UserModel(
            email="member@invite-test.com",
            password_hash=hasher.hash("Member1234!"),
            is_active=True,
        )
        member_user.roles.append(member_role)

        outsider_user = UserModel(
            email="outsider@invite-test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )

        db.session.add_all([admin_user, member_user, outsider_user])
        db.session.flush()  # assign user IDs before project references admin_user.id

        # Seed a project owned by admin
        project = ProjectModel(
            name="Invite Test Project",
            owner_id=admin_user.id,
        )
        db.session.add(project)
        db.session.commit()

        # Store IDs on app for use in fixtures
        test_app._test_admin_email = "admin@invite-test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_member_email = "member@invite-test.com"
        test_app._test_member_password = "Member1234!"
        test_app._test_outsider_email = "outsider@invite-test.com"
        test_app._test_outsider_password = "Outsider1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_member_role_id = str(member_role.id)
        test_app._test_admin_user_id = str(admin_user.id)
        test_app._test_member_user_id = str(member_user.id)

        # Add member_user as a project member so they can list invitations
        # (user_projects is an association table — no ORM model; use raw SQL)
        from sqlalchemy import text
        from datetime import datetime, timezone
        db.session.execute(
            text(
                "INSERT INTO user_projects "
                "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, :rid, NULL, :at) "
                "ON CONFLICT (user_id, project_id) DO NOTHING"
            ),
            {
                "uid": str(member_user.id),
                "pid": str(project.id),
                "rid": str(member_role.id),
                "at": datetime.now(timezone.utc),
            },
        )
        db.session.commit()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def inv_client(invitation_app):
    """Test client for invitation tests."""
    return invitation_app.test_client()


def _login(client, email: str, password: str) -> str:
    """Helper: login and return access token."""
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_admin_email, invitation_app._test_admin_password)


@pytest.fixture
def member_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_member_email, invitation_app._test_member_password)


@pytest.fixture
def outsider_token(inv_client, invitation_app):
    return _login(inv_client, invitation_app._test_outsider_email, invitation_app._test_outsider_password)
