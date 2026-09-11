"""H1 — resolver query-budget regression guard.

Before this fix, `SqlAlchemyAuthzReader` issued a fresh SQL statement for
every `company_role_for` / `project_company_id` call, with no per-request
cache. Listing N projects of the same company as a company admin therefore
cost O(N) resolver queries (one `company_role_for` + one `project_company_id`
per project) instead of O(1).

Counts statements via a `before_cursor_execute` listener, filtered to the
exact literal SQL shapes `SqlAlchemyAuthzReader` emits (see its module
docstring) — this deliberately does NOT match the ORM's full-row `SELECT
projects.id AS projects_id, projects.company_id AS projects_company_id, ...`
queries the rest of the request also issues, only the reader's narrow
`SELECT company_id FROM projects ...` / `SELECT role FROM
user_company_access ...` / `SELECT 1 FROM user_projects ...` shapes.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

# Literal substrings unique to SqlAlchemyAuthzReader's raw-text queries (both
# the SQLite-normalized and plain forms share these SELECT-clause fragments —
# only the WHERE clause differs by dialect). Deliberately narrow so the ORM's
# full-row project/company queries are never counted.
_RESOLVER_MARKERS = (
    "FROM user_company_access",
    "SELECT 1 FROM user_projects",
    "SELECT company_id FROM projects",
    "SELECT id, company_id FROM projects",
)


@contextmanager
def _count_resolver_statements(engine):
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        if any(marker in statement for marker in _RESOLVER_MARKERS):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _listener)


@pytest.fixture(scope="module")
def budget_app():
    """Fully wired app: one company, one admin (with a legacy project:read
    role, mirroring the read-only role every real sign-up gets), and a
    helper to add more projects mid-test."""
    from app import create_app, db
    from config import TestingConfig

    class BudgetTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(BudgetTestConfig)

    with test_app.app_context():
        db.create_all()
        now = datetime.now(timezone.utc)

        db.session.flush()

        admin_user = UserModel(email="qb_admin@test.com", is_active=True)
        db.session.add(admin_user)
        db.session.flush()

        company = CompanyModel(
            id=uuid4(),
            legal_name="Budget Co",
            address="1 rue Budget",
            created_by=admin_user.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_user.id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )

        project_1 = ProjectModel(name="Budget Project 1", owner_id=admin_user.id, company_id=company.id)
        db.session.add(project_1)
        db.session.commit()

        test_app._admin_id = admin_user.id
        test_app._company_id = company.id
        test_app._project_1_id = project_1.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(budget_app):
    return budget_app.test_client()


@pytest.fixture
def admin_h(client):
    return {"Authorization": f"Bearer {mint_access_token(client, 'qb_admin@test.com')}"}


def test_get_single_project_stays_within_a_small_absolute_budget(client, admin_h, budget_app):
    """(a) GET /projects/<id> for a company admin: the resolver issues exactly
    `project_company_id` + `company_role_for` = 2 statements once per request
    (memoized across the decorator's outer check, can_read_project, and the
    my_permissions serialization — all three share one `resolve_for_request`
    call). Budget of 3 leaves headroom for a future is_assigned/grants query
    without regressing to the old per-call-site multiplication.
    """
    from app import db

    with budget_app.app_context():
        engine = db.engine

    with _count_resolver_statements(engine) as statements:
        resp = client.get(f"/api/v1/projects/{budget_app._project_1_id}", headers=admin_h)

    assert resp.status_code == 200, resp.get_json()
    assert len(statements) <= 3, f"expected <=3 resolver statements, got {len(statements)}: {statements}"


def test_list_projects_query_count_does_not_grow_linearly(client, admin_h, budget_app):
    """(b) 5 projects of the same company must not cost ~5x the resolver
    statements of 1 project — the preload_project_company_ids batch fetch
    (H1) plus the per-request company_role_for cache collapse the per-project
    cost to a constant."""
    from app import db

    with budget_app.app_context():
        engine = db.engine

    with _count_resolver_statements(engine) as statements_one:
        resp1 = client.get("/api/v1/projects", headers=admin_h)
    assert resp1.status_code == 200, resp1.get_json()
    assert len(resp1.get_json()["projects"]) == 1
    count_one = len(statements_one)

    # Add 4 more projects to the SAME company.
    with budget_app.app_context():
        for i in range(4):
            db.session.add(
                ProjectModel(
                    name=f"Budget Project {i + 2}", owner_id=budget_app._admin_id, company_id=budget_app._company_id
                )
            )
        db.session.commit()

    with _count_resolver_statements(engine) as statements_five:
        resp5 = client.get("/api/v1/projects", headers=admin_h)
    assert resp5.status_code == 200, resp5.get_json()
    assert len(resp5.get_json()["projects"]) == 5
    count_five = len(statements_five)

    assert count_five <= count_one + 2, (
        f"resolver statement count grew ~linearly with project count: "
        f"1 project -> {count_one} ({statements_one}), 5 projects -> {count_five} ({statements_five})"
    )
