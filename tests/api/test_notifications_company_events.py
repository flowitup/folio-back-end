"""API tests for `company_events[]` in GET /notifications (Phase 2 onboarding, finding 12).

Pins the invariant that `count` covers only `items` + `attendance_pending` —
`company_events` must never bump the notification badge until both clients
ship UI for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def notif_app():
    from app import create_app, db
    from config import TestingConfig

    class NotifTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(NotifTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def notif_client(notif_app):
    return notif_app.test_client()


class _EmptyDueNotifications:
    """Fake replacing `list_due_notifications_usecase` — its real SQL (Postgres
    `::timestamp` / `AT TIME ZONE` / `INTERVAL` syntax) is not SQLite-portable
    (see `tests/api/test_notifications_endpoints.py::_needs_pg`); these tests
    only assert on `company_events`, so the due-notes list is stubbed empty."""

    def execute(self, **_kwargs):
        return []


@pytest.fixture(autouse=True)
def _stub_due_notifications(notif_app, monkeypatch):
    import wiring

    with notif_app.app_context():
        monkeypatch.setattr(wiring.get_container(), "list_due_notifications_usecase", _EmptyDueNotifications())
    yield


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


def _make_user(app, email: str) -> str:
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_company(app, admin_id, *, name: str):
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(), legal_name=name, address="1 rue", created_by=admin_id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        db.session.commit()
        return company.id


def _attach_unassigned_member(app, user_id, company_id, *, attached_days_ago: int = 1):
    """A member attached recently with a linked Person profile, and NO project assignment."""
    from app import db

    attached_at = datetime.now(timezone.utc) - timedelta(days=attached_days_ago)
    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id, company_id=company_id, role="member", is_primary=True, attached_at=attached_at
            )
        )
        person = PersonModel(
            id=uuid4(),
            name="New Member",
            normalized_name="new member",
            created_by_user_id=user_id,
            created_at=attached_at,
            user_id=user_id,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            CompanyPersonModel(
                id=uuid4(), company_id=company_id, person_id=person.id, is_active=True, created_at=attached_at
            )
        )
        db.session.commit()


class TestCompanyEvents:
    def test_admin_sees_unassigned_new_member(self, notif_client, notif_app):
        admin_id = _make_user(notif_app, "ce_admin1@test.com")
        member_id = _make_user(notif_app, "ce_member1@test.com")
        company_id = _make_company(notif_app, admin_id, name="CE Co 1")
        _attach_unassigned_member(notif_app, member_id, company_id)
        token = _login(notif_client, "ce_admin1@test.com")

        resp = notif_client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert any(e["user_id"] == str(member_id) for e in body["company_events"])
        # count must NOT include company_events (finding 12).
        assert body["count"] == len(body["items"]) + len(body["attendance_pending"])

    def test_assigned_member_does_not_appear(self, notif_client, notif_app):
        admin_id = _make_user(notif_app, "ce_admin2@test.com")
        member_id = _make_user(notif_app, "ce_member2@test.com")
        company_id = _make_company(notif_app, admin_id, name="CE Co 2")
        _attach_unassigned_member(notif_app, member_id, company_id)

        from app import db
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        with notif_app.app_context():
            project = ProjectModel(id=uuid4(), name="CE Project", owner_id=admin_id, company_id=company_id)
            db.session.add(project)
            db.session.flush()
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, invited_by_user_id, assigned_at) "
                    "VALUES (:uid, :pid, NULL, :at)"
                ),
                {"uid": str(member_id), "pid": str(project.id), "at": now},
            )
            db.session.commit()

        token = _login(notif_client, "ce_admin2@test.com")
        resp = notif_client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert not any(e["user_id"] == str(member_id) for e in body["company_events"])
        assert body["count"] == len(body["items"]) + len(body["attendance_pending"])

    def test_old_attachment_does_not_appear(self, notif_client, notif_app):
        admin_id = _make_user(notif_app, "ce_admin3@test.com")
        member_id = _make_user(notif_app, "ce_member3@test.com")
        company_id = _make_company(notif_app, admin_id, name="CE Co 3")
        _attach_unassigned_member(notif_app, member_id, company_id, attached_days_ago=30)
        token = _login(notif_client, "ce_admin3@test.com")

        resp = notif_client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert not any(e["user_id"] == str(member_id) for e in body["company_events"])
        assert body["count"] == len(body["items"]) + len(body["attendance_pending"])

    def test_non_admin_sees_no_company_events(self, notif_client, notif_app):
        admin_id = _make_user(notif_app, "ce_admin4@test.com")
        member_id = _make_user(notif_app, "ce_member4@test.com")
        company_id = _make_company(notif_app, admin_id, name="CE Co 4")
        _attach_unassigned_member(notif_app, member_id, company_id)
        token = _login(notif_client, "ce_member4@test.com")

        resp = notif_client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["company_events"] == []
        assert body["count"] == len(body["items"]) + len(body["attendance_pending"])
