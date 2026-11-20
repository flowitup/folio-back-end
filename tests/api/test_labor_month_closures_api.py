"""API tests for closed labor months ("Chốt tháng", owner decision QĐ-10).

Closing is a marker, not a lock: every write into a closed month still succeeds,
and is recorded (who, when, what kind, which entry/worker) on the closure.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.domain.time import business_today
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
from app.infrastructure.database.models.worker import WorkerModel
from tests.auth_login_helper import mint_access_token
from tests.company_tenancy_helper import company_for_projects, seed_company_tenancy

CLOSED = "2026-09"
OPEN = "2026-08"


@pytest.fixture(scope="module")
def closure_app():
    from sqlalchemy import text

    from app import create_app, db
    from config import TestingConfig

    class ClosureTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(ClosureTestConfig)

    with test_app.app_context():
        db.create_all()

        admin = UserModel(email="close_admin@test.com", is_active=True, display_name="Admin An")
        manager = UserModel(email="close_manager@test.com", is_active=True, display_name="Bob")
        member = UserModel(email="close_member@test.com", is_active=True)
        no_pay = UserModel(email="close_nopay@test.com", is_active=True)
        db.session.add_all([admin, manager, member, no_pay])
        db.session.flush()

        company_id = company_for_projects(db.session, admin.id)
        project = ProjectModel(name="Closure P", owner_id=admin.id, company_id=company_id)
        other = ProjectModel(name="Closure Q", owner_id=admin.id, company_id=company_id)
        db.session.add_all([project, other])
        db.session.flush()

        ana = WorkerModel(project_id=project.id, name="Ana", daily_rate=100)
        bao = WorkerModel(project_id=project.id, name="Bao", daily_rate=120)
        # The member's own worker row, for self-logging.
        cuong = WorkerModel(project_id=project.id, name="Cuong", daily_rate=90, user_id=member.id)
        db.session.add_all([ana, bao, cuong])
        db.session.flush()

        for uid in (manager.id, member.id, no_pay.id):
            db.session.execute(
                text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
                {"uid": str(uid), "pid": str(project.id), "at": datetime.now(timezone.utc)},
            )
        db.session.commit()

        test_app._project_id = str(project.id)
        test_app._other_project_id = str(other.id)
        test_app._ana = str(ana.id)
        test_app._bao = str(bao.id)
        test_app._cuong = str(cuong.id)
        test_app._no_pay_id = no_pay.id
        roles = {admin.id: "admin", manager.id: "manager", member.id: "member", no_pay.id: "manager"}

    seed_company_tenancy(test_app, roles=roles)

    # A manager whose pay view is denied (D8): the deny implies no manage_labor either.
    with test_app.app_context():
        db.session.add(
            CompanyMemberGrantModel(
                id=uuid4(),
                company_id=company_id,
                user_id=test_app._no_pay_id,
                permission="project:view_pay",
                effect="deny",
                project_id=None,
                granted_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()

    yield test_app

    with test_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def client(closure_app):
    return closure_app.test_client()


def _h(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


@pytest.fixture(scope="module")
def admin_h(client):
    return _h(client, "close_admin@test.com")


@pytest.fixture(scope="module")
def manager_h(client):
    return _h(client, "close_manager@test.com")


@pytest.fixture(scope="module")
def member_h(client):
    return _h(client, "close_member@test.com")


@pytest.fixture(scope="module")
def no_pay_h(client):
    return _h(client, "close_nopay@test.com")


def _base(app) -> str:
    return f"/api/v1/projects/{app._project_id}"


def _close(client, app, headers, month=CLOSED):
    return client.post(f"{_base(app)}/labor-month-closures", json={"month": month}, headers=headers)


def _reopen(client, app, headers, month=CLOSED):
    return client.delete(f"{_base(app)}/labor-month-closures/{month}", headers=headers)


def _closure(client, app, headers, month=CLOSED):
    resp = client.get(f"{_base(app)}/labor-month-closures?month={month}", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    closures = resp.get_json()["closures"]
    return closures[0] if closures else None


@pytest.fixture()
def closed_month(client, closure_app, admin_h):
    """CLOSED is closed by the admin for the test, and reopened afterwards."""
    resp = _close(client, closure_app, admin_h)
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    yield
    _reopen(client, closure_app, admin_h)


# ---------------------------------------------------------------------------
# Close / reopen / read
# ---------------------------------------------------------------------------


def test_close_records_who_and_when_then_reopen_undoes_it(client, closure_app, manager_h, admin_h):
    created = _close(client, closure_app, manager_h)
    assert created.status_code == 201, created.get_data(as_text=True)
    body = created.get_json()
    assert body["month"] == CLOSED
    assert body["closed_by_name"] == "Bob"
    assert body["closed_at"]
    assert body["edited_after_close"] == 0
    assert body["edits"] == []

    # Idempotent: a second close answers 200 with the same closure.
    again = _close(client, closure_app, admin_h)
    assert again.status_code == 200
    assert again.get_json()["closed_by_name"] == "Bob"

    listed = client.get(f"{_base(closure_app)}/labor-month-closures", headers=admin_h).get_json()["closures"]
    assert [c["month"] for c in listed] == [CLOSED]

    assert _reopen(client, closure_app, manager_h).status_code == 204
    assert _closure(client, closure_app, admin_h) is None
    assert _reopen(client, closure_app, manager_h).status_code == 404


def test_a_future_month_cannot_be_closed(client, closure_app, admin_h):
    today = business_today()
    nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    resp = _close(client, closure_app, admin_h, month=nxt.strftime("%Y-%m"))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "month_in_future"


@pytest.mark.parametrize("month", ["2026-13", "2026-9", "2026-09-01", ""])
def test_a_bad_month_is_rejected(client, closure_app, admin_h, month):
    assert _close(client, closure_app, admin_h, month=month).status_code == 400


def test_bad_month_in_path_or_query_is_400(client, closure_app, admin_h):
    assert _reopen(client, closure_app, admin_h, month="sept").status_code == 400
    assert client.get(f"{_base(closure_app)}/labor-month-closures?month=x", headers=admin_h).status_code == 400


def test_members_and_pay_denied_managers_cannot_close_reopen_or_read(
    client, closure_app, member_h, no_pay_h, closed_month
):
    for headers in (member_h, no_pay_h):
        assert _close(client, closure_app, headers, month=OPEN).status_code == 403
        assert _reopen(client, closure_app, headers).status_code == 403
        listed = client.get(f"{_base(closure_app)}/labor-month-closures", headers=headers)
        assert listed.status_code == 403


def test_closures_are_per_project(client, closure_app, admin_h, closed_month):
    other = client.get(f"/api/v1/projects/{closure_app._other_project_id}/labor-month-closures", headers=admin_h)
    assert other.status_code == 200
    assert other.get_json()["closures"] == []


# ---------------------------------------------------------------------------
# Writes into a closed month: never blocked, always recorded
# ---------------------------------------------------------------------------


def test_logging_editing_and_deleting_a_day_in_a_closed_month_is_recorded(
    client, closure_app, admin_h, manager_h, closed_month
):
    base = _base(closure_app)
    created = client.post(
        f"{base}/labor-entries",
        json={"worker_id": closure_app._ana, "date": f"{CLOSED}-25", "shift_type": "full"},
        headers=manager_h,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["month_closed"] is True
    assert created.get_json()["closed_months"] == [CLOSED]
    entry_id = created.get_json()["id"]

    edited = client.put(f"{base}/labor-entries/{entry_id}", json={"shift_type": "half"}, headers=manager_h)
    assert edited.status_code == 200, edited.get_data(as_text=True)
    assert edited.get_json()["month_closed"] is True

    assert client.delete(f"{base}/labor-entries/{entry_id}", headers=admin_h).status_code == 204

    closure = _closure(client, closure_app, admin_h)
    assert closure["edited_after_close"] == 3
    # Newest first.
    assert [e["kind"] for e in closure["edits"]] == ["entry_deleted", "entry_updated", "entry_created"]
    assert {e["entry_id"] for e in closure["edits"]} == {entry_id}
    assert {e["worker_name"] for e in closure["edits"]} == {"Ana"}
    assert {e["entry_date"] for e in closure["edits"]} == {f"{CLOSED}-25"}
    by_kind = {e["kind"]: e for e in closure["edits"]}
    assert by_kind["entry_created"]["user_name"] == "Bob"
    assert by_kind["entry_deleted"]["user_name"] == "Admin An"
    assert closure["last_edited_by_name"] == "Admin An"
    assert closure["last_edited_at"]


def test_a_day_in_an_open_month_is_not_recorded(client, closure_app, admin_h, manager_h, closed_month):
    created = client.post(
        f"{_base(closure_app)}/labor-entries",
        json={"worker_id": closure_app._ana, "date": f"{OPEN}-10", "shift_type": "full"},
        headers=manager_h,
    )
    assert created.status_code == 201
    assert "month_closed" not in created.get_json()
    assert _closure(client, closure_app, admin_h)["edited_after_close"] == 0
    client.delete(f"{_base(closure_app)}/labor-entries/{created.get_json()['id']}", headers=admin_h)


def test_bulk_logging_into_a_closed_month_records_each_worker(client, closure_app, admin_h, manager_h, closed_month):
    resp = client.post(
        f"{_base(closure_app)}/labor-entries/bulk",
        json={
            "date": f"{CLOSED}-26",
            "entries": [
                {"worker_id": closure_app._ana, "shift_type": "full"},
                {"worker_id": closure_app._bao, "shift_type": "full"},
            ],
        },
        headers=manager_h,
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["month_closed"] is True
    closure = _closure(client, closure_app, admin_h)
    assert closure["edited_after_close"] == 2
    assert {e["worker_name"] for e in closure["edits"]} == {"Ana", "Bao"}
    assert {e["kind"] for e in closure["edits"]} == {"entry_created"}
    for entry_id in resp.get_json()["created"]:
        client.delete(f"{_base(closure_app)}/labor-entries/{entry_id}", headers=admin_h)


def test_a_labor_payment_into_a_closed_month_is_recorded_and_moving_it_out_too(
    client, closure_app, admin_h, manager_h, closed_month
):
    base = _base(closure_app)
    body = {
        "type": "labor",
        "issue_date": f"{CLOSED}-30",
        "recipient_name": "Ana",
        "items": [{"description": "Wages", "quantity": 1, "unit_price": 100}],
        "service_month": f"{CLOSED}-01",
        "worker_id": closure_app._ana,
    }
    created = client.post(f"{base}/invoices", json=body, headers=manager_h)
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["month_closed"] is True
    invoice_id = created.get_json()["id"]

    moved = client.put(f"{base}/invoices/{invoice_id}", json={"service_month": f"{OPEN}-01"}, headers=manager_h)
    assert moved.status_code == 200, moved.get_data(as_text=True)
    assert moved.get_json()["month_closed"] is True  # it left the closed month

    # Now in an open month: editing or deleting it no longer touches the closed one.
    assert client.put(f"{base}/invoices/{invoice_id}", json={"notes": "x"}, headers=manager_h).status_code == 200
    assert client.delete(f"{base}/invoices/{invoice_id}", headers=manager_h).status_code == 204

    closure = _closure(client, closure_app, admin_h)
    assert sorted(e["kind"] for e in closure["edits"]) == ["payment_created", "payment_updated"]
    assert {e["entry_id"] for e in closure["edits"]} == {invoice_id}
    assert {e["worker_name"] for e in closure["edits"]} == {"Ana"}
    assert {e["entry_date"] for e in closure["edits"]} == {None}


def test_other_expenses_never_touch_a_closed_month(client, closure_app, admin_h, manager_h, closed_month):
    resp = client.post(
        f"{_base(closure_app)}/invoices",
        json={
            "type": "materials_services",
            "issue_date": f"{CLOSED}-15",
            "recipient_name": "Supplier",
            "items": [{"description": "Sand", "quantity": 1, "unit_price": 10}],
        },
        headers=manager_h,
    )
    assert resp.status_code == 201
    assert "month_closed" not in resp.get_json()
    assert _closure(client, closure_app, admin_h)["edited_after_close"] == 0


def test_a_backdated_rate_change_reprices_the_closed_month_and_is_recorded(
    client, closure_app, admin_h, manager_h, closed_month
):
    base = _base(closure_app)
    day = client.post(
        f"{base}/labor-entries",
        json={"worker_id": closure_app._bao, "date": f"{CLOSED}-10", "shift_type": "full"},
        headers=manager_h,
    )
    assert day.status_code == 201
    before = _closure(client, closure_app, admin_h)["edited_after_close"]

    rate = client.post(
        f"{base}/workers/{closure_app._bao}/rate-changes",
        json={"effective_date": f"{CLOSED}-05", "daily_rate": 150},
        headers=manager_h,
    )
    assert rate.status_code == 201, rate.get_data(as_text=True)
    assert rate.get_json()["month_closed"] is True

    # A rate dated after the closed month does not reach it.
    later = client.post(
        f"{base}/workers/{closure_app._bao}/rate-changes",
        json={"effective_date": "2026-10-01", "daily_rate": 160},
        headers=manager_h,
    )
    assert later.status_code == 201
    assert "month_closed" not in later.get_json()

    assert (
        client.delete(
            f"{base}/workers/{closure_app._bao}/rate-changes/{rate.get_json()['id']}", headers=manager_h
        ).status_code
        == 204
    )

    closure = _closure(client, closure_app, admin_h)
    assert closure["edited_after_close"] == before + 2
    kinds = [e["kind"] for e in closure["edits"] if e["kind"].startswith("rate")]
    assert sorted(kinds) == ["rate_change_deleted", "rate_changed"]
    assert {e["entry_date"] for e in closure["edits"] if e["kind"].startswith("rate")} == {f"{CLOSED}-05"}

    client.delete(f"{base}/workers/{closure_app._bao}/rate-changes/{later.get_json()['id']}", headers=manager_h)
    client.delete(f"{base}/labor-entries/{day.get_json()['id']}", headers=admin_h)


def test_a_rate_change_for_a_worker_with_no_day_in_the_closed_month_is_not_recorded(
    client, closure_app, admin_h, manager_h, closed_month
):
    base = _base(closure_app)
    rate = client.post(
        f"{base}/workers/{closure_app._ana}/rate-changes",
        json={"effective_date": f"{CLOSED}-01", "daily_rate": 110},
        headers=manager_h,
    )
    assert rate.status_code == 201
    assert "month_closed" not in rate.get_json()
    client.delete(f"{base}/workers/{closure_app._ana}/rate-changes/{rate.get_json()['id']}", headers=manager_h)


def test_self_logged_days_and_their_validation_in_a_closed_month_are_recorded(
    client, closure_app, admin_h, manager_h, member_h
):
    # Workers self-log within a ~month window, so this uses the current month.
    current = business_today().strftime("%Y-%m")
    assert _close(client, closure_app, admin_h, month=current).status_code in (200, 201)
    base = _base(closure_app)
    try:
        logged = client.post(
            f"{base}/labor-entries/self",
            json={"date": business_today().isoformat(), "shift_type": "full"},
            headers=member_h,
        )
        assert logged.status_code == 201, logged.get_data(as_text=True)
        assert logged.get_json()["month_closed"] is True
        entry_id = logged.get_json()["id"]

        validated = client.post(f"{base}/labor-entries/{entry_id}/validate", headers=manager_h)
        assert validated.status_code == 200, validated.get_data(as_text=True)
        assert validated.get_json()["month_closed"] is True

        closure = _closure(client, closure_app, admin_h, month=current)
        assert sorted(e["kind"] for e in closure["edits"]) == ["entry_self_logged", "entry_validated"]
        assert {e["worker_name"] for e in closure["edits"]} == {"Cuong"}
        client.delete(f"{base}/labor-entries/{entry_id}", headers=admin_h)
    finally:
        _reopen(client, closure_app, admin_h, month=current)


def test_reopening_drops_the_recorded_edits(client, closure_app, admin_h, manager_h):
    base = _base(closure_app)
    assert _close(client, closure_app, admin_h).status_code == 201
    day = client.post(
        f"{base}/labor-entries",
        json={"worker_id": closure_app._ana, "date": f"{CLOSED}-02", "shift_type": "full"},
        headers=manager_h,
    )
    assert _closure(client, closure_app, admin_h)["edited_after_close"] == 1
    assert _reopen(client, closure_app, admin_h).status_code == 204
    assert _close(client, closure_app, admin_h).status_code == 201
    assert _closure(client, closure_app, admin_h)["edited_after_close"] == 0
    _reopen(client, closure_app, admin_h)
    client.delete(f"{base}/labor-entries/{day.get_json()['id']}", headers=admin_h)
