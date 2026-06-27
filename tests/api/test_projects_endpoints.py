"""Tests for project budget/spent API endpoints and spent reader.

Covers:
- Create project with budget + budget_source echoes them in response.
- Create without budget → null budget, spent 0.
- PATCH only budget_source → budget unchanged (regression for description-drop landmine).
- PATCH budget → null → budget cleared.
- GET list + GET detail include budget/budget_source/spent.
- Spent reader: labor + non-released_funds invoices; refunds net down; batch map; no-rows → 0.
- Cross-check: spent == Σ tag-summary rows for same seed data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_project(client, token, *, name: str = "Budget Project", budget=None, budget_source=None):
    """POST /api/v1/projects and return (status_code, body)."""
    payload: dict = {"name": name}
    if budget is not None:
        payload["budget"] = budget
    if budget_source is not None:
        payload["budget_source"] = budget_source
    resp = client.post("/api/v1/projects", json=payload, headers=_auth(token))
    return resp.status_code, resp.get_json()


def _get_project(client, token, project_id: str):
    resp = client.get(f"/api/v1/projects/{project_id}", headers=_auth(token))
    return resp.status_code, resp.get_json()


def _update_project(client, token, project_id: str, payload: dict):
    resp = client.put(f"/api/v1/projects/{project_id}", json=payload, headers=_auth(token))
    return resp.status_code, resp.get_json()


# ---------------------------------------------------------------------------
# Budget field: create + read
# ---------------------------------------------------------------------------


def test_create_project_with_budget_echoes_fields(inv_client, superadmin_token):
    """Create project with budget + budget_source → response echoes them."""
    status, body = _create_project(
        inv_client, superadmin_token, name="Budget Echo", budget=50000.0, budget_source="Client Contract"
    )
    assert status == 201, body
    assert body["budget"] == pytest.approx(50000.0)
    assert body["budget_source"] == "Client Contract"
    assert body["spent"] == pytest.approx(0.0)


def test_create_project_without_budget_defaults_null(inv_client, superadmin_token):
    """Create without budget → null budget, spent 0."""
    status, body = _create_project(inv_client, superadmin_token, name="No Budget Project")
    assert status == 201, body
    assert body["budget"] is None
    assert body["budget_source"] is None
    assert body["spent"] == pytest.approx(0.0)


def test_get_project_includes_budget_fields(inv_client, admin_token, invitation_app):
    """GET detail includes budget/budget_source/spent."""
    # Use the seeded project (no budget set)
    pid = invitation_app._test_project_id
    status, body = _get_project(inv_client, admin_token, pid)
    assert status == 200, body
    assert "budget" in body
    assert "budget_source" in body
    assert "spent" in body


def test_list_projects_includes_budget_fields(inv_client, superadmin_token):
    """GET list includes budget/budget_source/spent on every project row."""
    resp = inv_client.get("/api/v1/projects", headers=_auth(superadmin_token))
    assert resp.status_code == 200
    projects = resp.get_json()["projects"]
    assert projects, "expected at least one project"
    for p in projects:
        assert "budget" in p
        assert "budget_source" in p
        assert "spent" in p


# ---------------------------------------------------------------------------
# Budget field: PATCH semantics (model_fields_set landmine)
# ---------------------------------------------------------------------------


def test_patch_only_budget_source_leaves_budget_unchanged(inv_client, superadmin_token):
    """PATCH of only budget_source must NOT wipe budget (regression)."""
    # Create with a budget
    status, created = _create_project(
        inv_client, superadmin_token, name="PATCH Regression", budget=99999.0, budget_source="Original Source"
    )
    assert status == 201, created
    pid = created["id"]

    # PATCH only budget_source — budget must survive
    status, updated = _update_project(inv_client, superadmin_token, pid, {"budget_source": "Updated Source"})
    assert status == 200, updated
    assert updated["budget"] == pytest.approx(99999.0), "budget was wiped — PATCH landmine!"
    assert updated["budget_source"] == "Updated Source"


def test_patch_budget_to_null_clears_it(inv_client, superadmin_token):
    """Explicitly setting budget=null via PATCH clears the budget."""
    status, created = _create_project(
        inv_client, superadmin_token, name="Clear Budget Test", budget=12345.0, budget_source="Funding"
    )
    assert status == 201, created
    pid = created["id"]

    status, updated = _update_project(inv_client, superadmin_token, pid, {"budget": None})
    assert status == 200, updated
    assert updated["budget"] is None


def test_patch_budget_source_only_persists_to_get(inv_client, superadmin_token):
    """After PATCH only budget_source, GET detail also shows preserved budget."""
    status, created = _create_project(
        inv_client, superadmin_token, name="Persist Check", budget=77777.0, budget_source="Old"
    )
    assert status == 201, created
    pid = created["id"]

    _update_project(inv_client, superadmin_token, pid, {"budget_source": "New"})
    status, body = _get_project(inv_client, superadmin_token, pid)
    assert status == 200, body
    assert body["budget"] == pytest.approx(77777.0)
    assert body["budget_source"] == "New"


# ---------------------------------------------------------------------------
# Spent reader: integration via seeded labor + invoice rows
# ---------------------------------------------------------------------------


@pytest.fixture
def spent_reader_project(invitation_app):
    """A project seeded with labor entry + invoice rows for spent reader tests."""
    from app import db
    from app.infrastructure.database.models import ProjectModel
    from app.infrastructure.database.models.invoice import InvoiceModel
    from app.infrastructure.database.models.labor_entry import LaborEntryModel
    from app.infrastructure.database.models.worker import WorkerModel

    with invitation_app.app_context():
        owner_id = UUID(invitation_app._test_admin_user_id)

        # Project
        project = ProjectModel(name="SpentReader Test", owner_id=owner_id)
        db.session.add(project)
        db.session.flush()

        # Worker with daily_rate=200
        worker = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            name="Test Worker",
            daily_rate=Decimal("200.00"),
            is_active=True,
        )
        db.session.add(worker)
        db.session.flush()

        # Labor entry: full day → effective_cost = 200
        labor_entry = LaborEntryModel(
            id=uuid4(),
            worker_id=worker.id,
            date=date(2025, 1, 10),
            shift_type="full",  # else → 1.0 multiplier
        )
        db.session.add(labor_entry)

        # Materials invoice: 2 items → 50*3 + 100*2 = 350
        invoice_ms = InvoiceModel(
            id=uuid4(),
            project_id=project.id,
            invoice_number="INV-001",
            type="materials_services",
            issue_date=date(2025, 1, 15),
            recipient_name="Supplier",
            items=[
                {"quantity": "3", "unit_price": "50"},
                {"quantity": "2", "unit_price": "100"},
            ],
        )
        db.session.add(invoice_ms)
        db.session.commit()

        yield {
            "project_id": project.id,
            "worker_id": worker.id,
            "labor_entry_id": labor_entry.id,
            "invoice_ms_id": invoice_ms.id,
        }

        with invitation_app.app_context():
            db.session.execute(
                __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
                {"id": str(project.id)},
            )
            db.session.commit()


def test_spent_reader_labor_plus_invoice(invitation_app, spent_reader_project):
    """spent = labor_cost (200) + invoice_total (350) = 550."""
    from app import db
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        reader = SqlAlchemyProjectSpentReader(db.session)
        pid = spent_reader_project["project_id"]
        result = reader.sum_spent_by_projects([pid])
        assert pid in result
        # labor: 200 (daily_rate * 1.0 multiplier, no override)
        # invoice: 3*50 + 2*100 = 350
        assert result[pid] == pytest.approx(Decimal("550.00"))


def test_spent_reader_released_funds_excluded(invitation_app, spent_reader_project):
    """Adding a released_funds invoice does NOT change spent."""
    from app import db
    from app.infrastructure.database.models.invoice import InvoiceModel
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        pid = spent_reader_project["project_id"]

        rf_invoice = InvoiceModel(
            id=uuid4(),
            project_id=pid,
            invoice_number="RF-001",
            type="released_funds",
            issue_date=date(2025, 2, 1),
            recipient_name="Client",
            items=[{"quantity": "1", "unit_price": "999999"}],
        )
        db.session.add(rf_invoice)
        db.session.commit()

        reader = SqlAlchemyProjectSpentReader(db.session)
        result = reader.sum_spent_by_projects([pid])
        # Must still be 550 — released_funds is excluded
        assert result[pid] == pytest.approx(Decimal("550.00"))

        db.session.delete(rf_invoice)
        db.session.commit()


def test_spent_reader_refund_decreases_spent(invitation_app, spent_reader_project):
    """Refund invoice with negative lines decreases spent."""
    from app import db
    from app.infrastructure.database.models.invoice import InvoiceModel
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        pid = spent_reader_project["project_id"]

        refund_invoice = InvoiceModel(
            id=uuid4(),
            project_id=pid,
            invoice_number="REF-001",
            type="refund",
            issue_date=date(2025, 3, 1),
            recipient_name="Supplier",
            items=[{"quantity": "1", "unit_price": "-100"}],
        )
        db.session.add(refund_invoice)
        db.session.commit()

        reader = SqlAlchemyProjectSpentReader(db.session)
        result = reader.sum_spent_by_projects([pid])
        # 550 - 100 = 450
        assert result[pid] == pytest.approx(Decimal("450.00"))

        db.session.delete(refund_invoice)
        db.session.commit()


def test_spent_reader_batch_two_projects(invitation_app, spent_reader_project):
    """Batch call with two projects returns correct per-project map."""
    from app import db
    from app.infrastructure.database.models import ProjectModel
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        owner_id = UUID(invitation_app._test_admin_user_id)
        p2 = ProjectModel(name="Empty Project for batch", owner_id=owner_id)
        db.session.add(p2)
        db.session.commit()

        pid1 = spent_reader_project["project_id"]
        pid2 = p2.id

        reader = SqlAlchemyProjectSpentReader(db.session)
        result = reader.sum_spent_by_projects([pid1, pid2])

        assert pid1 in result
        assert pid2 in result
        assert result[pid1] == pytest.approx(Decimal("550.00"))
        assert result[pid2] == Decimal("0")

        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
            {"id": str(pid2)},
        )
        db.session.commit()


def test_spent_reader_no_rows_returns_zero(invitation_app):
    """Project with no labor/invoices → spent = 0."""
    from app import db
    from app.infrastructure.database.models import ProjectModel
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        owner_id = UUID(invitation_app._test_admin_user_id)
        empty_p = ProjectModel(name="Truly Empty", owner_id=owner_id)
        db.session.add(empty_p)
        db.session.commit()

        reader = SqlAlchemyProjectSpentReader(db.session)
        result = reader.sum_spent_by_projects([empty_p.id])
        assert result[empty_p.id] == Decimal("0")

        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
            {"id": str(empty_p.id)},
        )
        db.session.commit()


def test_spent_reader_empty_list_returns_empty_dict(invitation_app):
    """Calling with an empty list returns {}."""
    from app import db
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    with invitation_app.app_context():
        reader = SqlAlchemyProjectSpentReader(db.session)
        result = reader.sum_spent_by_projects([])
        assert result == {}


def test_spent_cross_check_equals_tag_summary_totals(invitation_app, spent_reader_project):
    """spent == Σ tag-summary rows (labor_cost + expense_total) for the same project."""
    from app import db
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
        SqlAlchemyProjectTagRepository,
    )

    with invitation_app.app_context():
        pid = spent_reader_project["project_id"]

        reader = SqlAlchemyProjectSpentReader(db.session)
        tag_repo = SqlAlchemyProjectTagRepository(db.session)

        spent_result = reader.sum_spent_by_projects([pid])
        project_spent = spent_result[pid]

        # Tag-summary aggregates
        labor_by_tag = tag_repo.sum_labor_cost_by_tag(pid)
        expense_by_tag = tag_repo.sum_expense_by_tag(pid)

        tag_labor_total = sum(v[0] for v in labor_by_tag.values())
        tag_expense_total = sum(v[0] for v in expense_by_tag.values())
        tag_total = tag_labor_total + tag_expense_total

        assert project_spent == pytest.approx(
            tag_total
        ), f"spent reader {project_spent} != tag-summary total {tag_total}"


# ---------------------------------------------------------------------------
# Spent shown via API endpoint
# ---------------------------------------------------------------------------


def test_api_detail_spent_is_numeric(inv_client, admin_token, invitation_app):
    """GET /projects/<id> returns spent as a float."""
    pid = invitation_app._test_project_id
    status, body = _get_project(inv_client, admin_token, pid)
    assert status == 200, body
    assert isinstance(body["spent"], (int, float))


def test_api_list_spent_is_numeric(inv_client, admin_token):
    """GET /projects returns spent as a float on each row."""
    resp = inv_client.get("/api/v1/projects", headers=_auth(admin_token))
    assert resp.status_code == 200
    for p in resp.get_json()["projects"]:
        assert isinstance(p["spent"], (int, float))
