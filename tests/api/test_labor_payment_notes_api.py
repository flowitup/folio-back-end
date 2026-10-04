"""API tests for GET/PUT /projects/<id>/labor-payment-notes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.worker import WorkerModel
from tests.auth_login_helper import mint_access_token
from tests.company_tenancy_helper import company_for_projects, seed_company_tenancy


@pytest.fixture(scope="module")
def note_app():
    from sqlalchemy import text

    from app import create_app, db
    from config import TestingConfig

    class NoteTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(NoteTestConfig)

    with test_app.app_context():
        db.create_all()

        admin = UserModel(email="paynote_admin@test.com", is_active=True)
        member = UserModel(email="paynote_member@test.com", is_active=True)
        db.session.add_all([admin, member])
        db.session.flush()

        company_id = company_for_projects(db.session, admin.id)
        project = ProjectModel(name="Notes P", owner_id=admin.id, company_id=company_id)
        other = ProjectModel(name="Notes Q", owner_id=admin.id, company_id=company_id)
        db.session.add_all([project, other])
        db.session.flush()

        worker = WorkerModel(project_id=project.id, name="Ana", daily_rate=100)
        foreign_worker = WorkerModel(project_id=other.id, name="Bao", daily_rate=100)
        db.session.add_all([worker, foreign_worker])
        db.session.flush()

        db.session.execute(
            text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
            {"uid": str(member.id), "pid": str(project.id), "at": datetime.now(timezone.utc)},
        )
        db.session.commit()

        test_app._project_id = str(project.id)
        test_app._worker_id = str(worker.id)
        test_app._foreign_worker_id = str(foreign_worker.id)
        admin_id, member_id = admin.id, member.id

    seed_company_tenancy(test_app, roles={admin_id: "admin", member_id: "member"})

    yield test_app

    with test_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def client(note_app):
    return note_app.test_client()


def _h(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


@pytest.fixture(scope="module")
def admin_h(client):
    return _h(client, "paynote_admin@test.com")


@pytest.fixture(scope="module")
def member_h(client):
    return _h(client, "paynote_member@test.com")


def _url(app) -> str:
    return f"/api/v1/projects/{app._project_id}/labor-payment-notes"


def _put(client, app, headers, note: str, month: str = "2026-09", worker_id: str | None = None):
    return client.put(
        _url(app),
        json={"worker_id": worker_id or app._worker_id, "month": month, "note": note},
        headers=headers,
    )


def test_put_creates_then_updates_and_get_lists_by_month(client, note_app, admin_h):
    created = _put(client, note_app, admin_h, "  waiting for the client transfer ")
    assert created.status_code == 200, created.get_data(as_text=True)
    body = created.get_json()
    assert body["note"] == "waiting for the client transfer"
    assert body["month"] == "2026-09"
    assert body["worker_id"] == note_app._worker_id

    updated = _put(client, note_app, admin_h, "paid in cash on the 12th")
    assert updated.status_code == 200
    assert updated.get_json()["id"] == body["id"]

    _put(client, note_app, admin_h, "august note", month="2026-08")

    sept = client.get(f"{_url(note_app)}?month=2026-09", headers=admin_h)
    assert sept.status_code == 200
    assert [n["note"] for n in sept.get_json()["notes"]] == ["paid in cash on the 12th"]

    every = client.get(_url(note_app), headers=admin_h)
    assert [n["month"] for n in every.get_json()["notes"]] == ["2026-08", "2026-09"]


def test_blank_note_clears_the_row(client, note_app, admin_h):
    _put(client, note_app, admin_h, "temporary", month="2026-07")

    cleared = _put(client, note_app, admin_h, "  ", month="2026-07")
    assert cleared.status_code == 200
    assert cleared.get_json() == {
        "worker_id": note_app._worker_id,
        "month": "2026-07",
        "note": None,
        "deleted": True,
    }
    listed = client.get(f"{_url(note_app)}?month=2026-07", headers=admin_h)
    assert listed.get_json()["notes"] == []


def test_worker_from_another_project_is_404(client, note_app, admin_h):
    resp = _put(client, note_app, admin_h, "x", worker_id=note_app._foreign_worker_id)
    assert resp.status_code == 404
    assert _put(client, note_app, admin_h, "x", worker_id=str(uuid4())).status_code == 404


@pytest.mark.parametrize("month", ["2026-13", "2026-9", "2026-09-01", ""])
def test_bad_month_is_rejected(client, note_app, admin_h, month):
    assert _put(client, note_app, admin_h, "x", month=month).status_code == 400


def test_bad_month_query_is_400(client, note_app, admin_h):
    assert client.get(f"{_url(note_app)}?month=sept", headers=admin_h).status_code == 400


def test_note_over_2000_chars_is_rejected(client, note_app, admin_h):
    assert _put(client, note_app, admin_h, "a" * 2001).status_code == 400


def test_member_can_read_but_not_write(client, note_app, member_h):
    assert client.get(_url(note_app), headers=member_h).status_code == 200
    assert _put(client, note_app, member_h, "nope").status_code == 403
