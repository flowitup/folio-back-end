"""A manager runs the spend side of a project, never its financing side.

`project:view_budget` splits project money in two. The spend side — materials &
services, labor payments, the `spent`/`labor_*` rollups — stays on
`project:manage_labor` / `project:view_pay`, so an assigned manager reads all of
it. The financing side — `budget`, `budget_source` and every `released_funds`
invoice with the aggregates derived from them — needs `project:view_budget`,
which the matrix grants to company admins only.

Reads narrow rather than refuse (an unreadable release is simply not in the
caller's list, and 404s by id); writes refuse outright, because recording,
retyping or deleting a release you cannot read is a blind edit. A company admin
re-opens the whole financing side for one manager with a single D8 grant row.

The xlsx/pdf export applies the same rule through `ExportInvoicesRequest`'s
`exclude_types`; that route's use case is not wired into this app fixture, so it
is covered in tests/unit/invoice_export/test_use_case.py instead.

Three callers over one project, differing only in what the matrix and the D8
table say about them:
  * the shared fixture's admin — company `admin`, holds the permission;
  * `bs_manager` — company `manager`, does not;
  * `bs_grantee` — company `manager` plus a D8 grant of `project:view_budget`
    on this project, so any difference from `bs_manager` is that row alone.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"
BUDGET = 50000.0
RELEASE_AMOUNT = 1200.0
EXPENSE_AMOUNT = 300.0

MANAGER_EMAIL = "bs_manager@invite-test.com"
GRANTEE_EMAIL = "bs_grantee@invite-test.com"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _invoice_body(invoice_type: str, unit_price: float, recipient_name: str = "Client Co") -> dict:
    return {
        "type": invoice_type,
        "issue_date": date.today().isoformat(),
        "recipient_name": recipient_name,
        "items": [{"description": "Line item", "quantity": 1, "unit_price": unit_price}],
    }


# ---------------------------------------------------------------------------
# Fixtures — module-scoped app (a per-test Flask app costs ~10s and blows the
# CI lint-test budget); every test below only reads, or writes rows it deletes.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bs_app(invitation_app):
    """Give `_test_project_id` a budget and a company with one manager + one grantee."""
    from app import db

    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.database.models.associations import user_projects

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        hasher = Argon2PasswordHasher()
        admin_id = UUID(invitation_app._test_admin_user_id)
        project_id = UUID(invitation_app._test_project_id)

        manager_user = UserModel(email=MANAGER_EMAIL, password_hash=hasher.hash(PASSWORD), is_active=True)
        grantee_user = UserModel(email=GRANTEE_EMAIL, password_hash=hasher.hash(PASSWORD), is_active=True)
        db.session.add_all([manager_user, grantee_user])
        db.session.flush()

        company = CompanyModel(
            id=uuid4(),
            legal_name="Budget Scope Co",
            address="1 rue du Budget",
            created_by=admin_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=admin_id, company_id=company.id, role="admin", is_primary=False, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=manager_user.id, company_id=company.id, role="manager", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=grantee_user.id, company_id=company.id, role="manager", is_primary=True, attached_at=now
                ),
                CompanyMemberGrantModel(
                    id=uuid4(),
                    company_id=company.id,
                    user_id=grantee_user.id,
                    permission="project:view_budget",
                    effect="grant",
                    project_id=project_id,
                    granted_by_user_id=admin_id,
                    granted_at=now,
                ),
            ]
        )

        project_row = db.session.get(ProjectModel, project_id)
        project_row.company_id = company.id
        project_row.budget = BUDGET
        project_row.budget_source = "Client Contract"

        # Insert through the table object, not raw text: on SQLite a UUID column
        # holds the 32-hex form, so a hand-written string literal would never
        # match `list_for_user_and_companies`'s ORM comparison and the project
        # would be missing from the caller's list.
        assigned = {
            row[0]
            for row in db.session.execute(
                user_projects.select().where(user_projects.c.project_id == project_id)
            ).fetchall()
        }
        for uid in (admin_id, manager_user.id, grantee_user.id):
            if uid not in assigned:
                db.session.execute(user_projects.insert().values(user_id=uid, project_id=project_id, assigned_at=now))
        db.session.commit()

    return invitation_app


@pytest.fixture(scope="module")
def bs_client(bs_app):
    return bs_app.test_client()


def _login(client, email: str, password: str = PASSWORD) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def admin_h(bs_client, bs_app):
    return _auth(_login(bs_client, bs_app._test_admin_email, bs_app._test_admin_password))


@pytest.fixture(scope="module")
def manager_h(bs_client):
    return _auth(_login(bs_client, MANAGER_EMAIL))


@pytest.fixture(scope="module")
def grantee_h(bs_client):
    return _auth(_login(bs_client, GRANTEE_EMAIL))


@pytest.fixture(scope="module")
def seeded_money(bs_client, bs_app, admin_h):
    """One release and one expense, both booked by the admin. Returns their ids."""
    pid = bs_app._test_project_id
    url = f"/api/v1/projects/{pid}/invoices"
    release = bs_client.post(url, json=_invoice_body("released_funds", RELEASE_AMOUNT), headers=admin_h)
    assert release.status_code == 201, release.get_data(as_text=True)
    expense = bs_client.post(url, json=_invoice_body("materials_services", EXPENSE_AMOUNT), headers=admin_h)
    assert expense.status_code == 201, expense.get_data(as_text=True)
    return {"release": release.get_json()["id"], "expense": expense.get_json()["id"]}


# ---------------------------------------------------------------------------
# Budget field on the project
# ---------------------------------------------------------------------------


def test_admin_reads_the_budget(bs_client, bs_app, admin_h):
    body = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}", headers=admin_h).get_json()
    assert body["budget"] == pytest.approx(BUDGET)
    assert body["budget_source"] == "Client Contract"
    assert "project:view_budget" in body["my_permissions"]


def test_manager_reads_spend_but_not_the_budget(bs_client, bs_app, admin_h, manager_h, seeded_money):
    body = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}", headers=manager_h).get_json()
    assert body["budget"] is None and body["budget_source"] is None
    assert "project:view_budget" not in body["my_permissions"]
    # The spend side is untouched: the expense the admin booked is still counted.
    assert body["spent"] == pytest.approx(EXPENSE_AMOUNT)


def test_project_list_hides_the_budget_from_a_manager(bs_client, bs_app, manager_h, seeded_money):
    rows = bs_client.get("/api/v1/projects", headers=manager_h).get_json()["projects"]
    row = next(p for p in rows if p["id"] == bs_app._test_project_id)
    assert row["budget"] is None and row["budget_source"] is None
    assert row["spent"] == pytest.approx(EXPENSE_AMOUNT)


def test_a_grant_gives_one_manager_the_budget_back(bs_client, bs_app, grantee_h):
    body = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}", headers=grantee_h).get_json()
    assert body["budget"] == pytest.approx(BUDGET)
    assert body["budget_source"] == "Client Contract"


# ---------------------------------------------------------------------------
# Writing the budget follows reading it
# ---------------------------------------------------------------------------


def test_manager_cannot_change_the_budget(bs_client, bs_app, manager_h):
    resp = bs_client.put(
        f"/api/v1/projects/{bs_app._test_project_id}",
        json={"budget": 1.0},
        headers=manager_h,
    )
    assert resp.status_code == 403


def test_manager_can_still_update_the_rest_of_the_project(bs_client, bs_app, manager_h):
    """The guard is scoped to the budget fields — project:update itself is intact."""
    resp = bs_client.put(
        f"/api/v1/projects/{bs_app._test_project_id}",
        json={"address": "2 rue du Budget"},
        headers=manager_h,
    )
    assert resp.status_code == 200
    # …and the response body never leaks the budget back to them.
    assert resp.get_json()["budget"] is None


def test_grantee_can_change_the_budget(bs_client, bs_app, grantee_h):
    resp = bs_client.put(
        f"/api/v1/projects/{bs_app._test_project_id}",
        json={"budget": BUDGET, "budget_source": "Client Contract"},
        headers=grantee_h,
    )
    assert resp.status_code == 200
    assert resp.get_json()["budget"] == pytest.approx(BUDGET)


# ---------------------------------------------------------------------------
# Released funds in the invoice list
# ---------------------------------------------------------------------------


def test_admin_sees_releases_and_their_totals(bs_client, bs_app, admin_h, seeded_money):
    body = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}/invoices", headers=admin_h).get_json()
    assert seeded_money["release"] in {i["id"] for i in body["invoices"]}
    assert body["funds_released_total"] == pytest.approx(RELEASE_AMOUNT)


def test_manager_gets_the_list_without_releases_or_their_totals(bs_client, bs_app, manager_h, seeded_money):
    body = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}/invoices", headers=manager_h).get_json()
    ids = {i["id"] for i in body["invoices"]}
    assert seeded_money["release"] not in ids
    # The expense side is still there — this is a narrowing, not a blackout.
    assert seeded_money["expense"] in ids
    assert body["funds_released_total"] == 0.0
    assert body["funds_released_company_total"] == 0.0
    assert body["funds_released_personal_total"] == 0.0
    assert body["company_cash_advanced_total"] == 0.0


def test_explicit_released_funds_filter_is_empty_for_a_manager(bs_client, bs_app, manager_h, seeded_money):
    """An empty list, not a 403: project:read still allows the call."""
    resp = bs_client.get(f"/api/v1/projects/{bs_app._test_project_id}/invoices?type=released_funds", headers=manager_h)
    assert resp.status_code == 200
    assert resp.get_json()["invoices"] == []


def test_grantee_sees_releases_again(bs_client, bs_app, grantee_h, seeded_money):
    body = bs_client.get(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices?type=released_funds", headers=grantee_h
    ).get_json()
    assert seeded_money["release"] in {i["id"] for i in body["invoices"]}
    assert body["funds_released_total"] == pytest.approx(RELEASE_AMOUNT)


def test_manager_cannot_open_a_release_by_id(bs_client, bs_app, manager_h, seeded_money):
    """404, not 403 — the response says nothing about whether the id exists."""
    resp = bs_client.get(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices/{seeded_money['release']}", headers=manager_h
    )
    assert resp.status_code == 404


def test_manager_can_still_open_an_expense_by_id(bs_client, bs_app, manager_h, seeded_money):
    resp = bs_client.get(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices/{seeded_money['expense']}", headers=manager_h
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Writing releases follows reading them
# ---------------------------------------------------------------------------


def test_manager_cannot_record_a_release(bs_client, bs_app, manager_h):
    resp = bs_client.post(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices",
        json=_invoice_body("released_funds", 10.0),
        headers=manager_h,
    )
    assert resp.status_code == 403


def test_manager_can_still_record_an_expense(bs_client, bs_app, manager_h):
    """project:manage_invoices is untouched on the spend side."""
    url = f"/api/v1/projects/{bs_app._test_project_id}/invoices"
    resp = bs_client.post(url, json=_invoice_body("materials_services", 10.0), headers=manager_h)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    # Drop the row straight from the DB so the module's spend totals stay put —
    # this app fixture wires no delete use case.
    from app import db

    from app.infrastructure.database.models.invoice import InvoiceModel

    with bs_app.app_context():
        db.session.delete(db.session.get(InvoiceModel, UUID(resp.get_json()["id"])))
        db.session.commit()


def test_manager_cannot_edit_a_release(bs_client, bs_app, manager_h, seeded_money):
    resp = bs_client.put(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices/{seeded_money['release']}",
        json={"recipient_name": "Rewritten"},
        headers=manager_h,
    )
    assert resp.status_code == 403


def test_manager_cannot_retype_an_expense_into_a_release(bs_client, bs_app, manager_h, seeded_money):
    """The guard reads both sides of the edit, so the type cannot be used as a way in."""
    resp = bs_client.put(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices/{seeded_money['expense']}",
        json={"type": "released_funds"},
        headers=manager_h,
    )
    assert resp.status_code == 403


def test_manager_cannot_delete_a_release(bs_client, bs_app, manager_h, seeded_money):
    resp = bs_client.delete(
        f"/api/v1/projects/{bs_app._test_project_id}/invoices/{seeded_money['release']}", headers=manager_h
    )
    assert resp.status_code == 403
