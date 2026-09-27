"""A deactivated account is flagged in the company list and cannot be given a task."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def inactive_user_id(invitation_app) -> str:
    from app import db
    from app.infrastructure.database.models import UserModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    user = UserModel(email="deactivated@invite-test.com", is_active=False)
    db.session.add(user)
    db.session.flush()
    db.session.add(
        UserCompanyAccessModel(
            user_id=user.id,
            company_id=UUID(invitation_app._test_company_id),
            role="member",
            is_primary=True,
            attached_at=datetime.now(timezone.utc),
        )
    )
    db.session.commit()
    return str(user.id)


def test_attached_users_flag_the_deactivated_account(inv_client, admin_token, invitation_app, inactive_user_id):
    resp = inv_client.get(
        f"/api/v1/companies/{invitation_app._test_company_id}/attached-users", headers=_auth(admin_token)
    )
    assert resp.status_code == 200
    by_id = {row["user_id"]: row["is_active"] for row in resp.get_json()["items"]}
    assert by_id[inactive_user_id] is False
    assert by_id[invitation_app._test_member_user_id] is True


def test_deactivated_account_cannot_be_given_a_task(inv_client, admin_token, invitation_app, inactive_user_id):
    resp = inv_client.post(
        f"/api/v1/projects/{invitation_app._test_project_id}/tasks",
        json={"title": "Pour slab", "assignee_id": inactive_user_id},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    assert "deactivated" in resp.get_json()["message"]
