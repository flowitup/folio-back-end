"""Inviting an existing account by email follows the assignment rules.

A manager may add a plain company member (or someone from outside the
company) but not a company manager or admin, exactly like
`PUT /projects/<id>/assignments/<user>`. A directly added account is attached
to the project's company AND listed in its directory, so the assign-member
pickers find it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.auth_login_helper import mint_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def people(invitation_app):
    """A project of the fixture company with an assigned manager, plus candidate invitees."""
    from app import db
    from app.infrastructure.database.models import ProjectModel, UserModel
    from app.infrastructure.database.models.associations import user_projects
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    company_id = UUID(invitation_app._test_company_id)
    tag = uuid4().hex[:6]
    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        users = {
            name: UserModel(email=f"{name}_{tag}@invite-rules.com", is_active=True)
            for name in ("manager", "other_manager", "plain_member", "stranger")
        }
        db.session.add_all(users.values())
        db.session.flush()
        project = ProjectModel(
            name="Direct add rules", owner_id=UUID(invitation_app._test_admin_user_id), company_id=company_id
        )
        db.session.add(project)
        db.session.flush()
        for name, role in (("manager", "manager"), ("other_manager", "manager"), ("plain_member", "member")):
            db.session.add(
                UserCompanyAccessModel(
                    user_id=users[name].id, company_id=company_id, role=role, is_primary=True, attached_at=now
                )
            )
        db.session.execute(user_projects.insert().values(user_id=users["manager"].id, project_id=project.id))
        db.session.commit()
        return {
            "project_id": str(project.id),
            "company_id": str(company_id),
            **{name: (str(u.id), u.email) for name, u in users.items()},
        }


def _invite(client, token, project_id, email):
    return client.post("/api/v1/invitations", json={"project_id": project_id, "email": email}, headers=_auth(token))


def _members(app, project_id):
    from app import db

    with app.app_context():
        rows = db.session.execute(
            text("SELECT user_id FROM user_projects WHERE REPLACE(CAST(project_id AS TEXT), '-', '') = :pid"),
            {"pid": project_id.replace("-", "")},
        ).fetchall()
    return {str(r[0]).replace("-", "") for r in rows}


def test_manager_cannot_add_a_company_manager_by_email(inv_client, invitation_app, people):
    token = mint_access_token(inv_client, people["manager"][1])
    resp = _invite(inv_client, token, people["project_id"], people["other_manager"][1])
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert people["other_manager"][0].replace("-", "") not in _members(invitation_app, people["project_id"])


def test_manager_can_add_a_plain_member_by_email(inv_client, invitation_app, people):
    token = mint_access_token(inv_client, people["manager"][1])
    resp = _invite(inv_client, token, people["project_id"], people["plain_member"][1])
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["kind"] == "direct_added"


def test_admin_can_add_a_company_manager_by_email(inv_client, admin_token, people):
    resp = _invite(inv_client, admin_token, people["project_id"], people["other_manager"][1])
    assert resp.status_code == 201, resp.get_data(as_text=True)


def test_directly_added_outsider_is_listed_in_the_company_directory(inv_client, admin_token, invitation_app, people):
    from tests.api.test_company_attachment_directory_invariant import _profiles

    resp = _invite(inv_client, admin_token, people["project_id"], people["stranger"][1])
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert people["stranger"][0].replace("-", "") in _profiles(invitation_app, people["company_id"])
