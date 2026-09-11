"""Invariant: attaching a user to a company lists them in its directory.

The assign-member pickers read `company_persons`, so a user with a
`user_company_access` row but no profile is attached and yet unassignable. Every
attach path must produce one: company creation, join code (see
tests/api/test_boot_cleanup_and_join_code_rotation.py::TestJoinCodeCreatesCompanyPerson) —
and the platform-ops migration backfills the rows that predate the rule.
"""

from __future__ import annotations

import pytest

from tests.auth_login_helper import mint_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _profiles(app, company_id: str) -> list[str]:
    """User ids linked to an active profile of `company_id`."""
    from app import db
    from sqlalchemy import text

    with app.app_context():
        rows = db.session.execute(
            text(
                "SELECT p.user_id FROM company_persons cp JOIN persons p ON "
                "REPLACE(LOWER(CAST(p.id AS TEXT)), '-', '') = REPLACE(LOWER(CAST(cp.person_id AS TEXT)), '-', '') "
                "WHERE REPLACE(LOWER(CAST(cp.company_id AS TEXT)), '-', '') = :cid AND cp.is_active"
            ),
            {"cid": company_id.replace("-", "").lower()},
        ).fetchall()
    return [str(r[0]).replace("-", "").lower() for r in rows if r[0] is not None]


def _create_company(client, token, name: str) -> dict:
    resp = client.post(
        "/api/v1/companies",
        json={"legal_name": name, "address": "1 rue du Test"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


@pytest.fixture
def creator(inv_client, invitation_app):
    """A fresh account with no company, used as the attaching user."""
    from uuid import uuid4

    from app import db
    from app.infrastructure.database.models import UserModel

    email = f"attach_{uuid4().hex[:8]}@test.com"
    with invitation_app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True)
        db.session.add(user)
        db.session.commit()
        user_id = str(user.id)

    token = mint_access_token(inv_client, email)
    return {"id": user_id.replace("-", "").lower(), "token": token}


def test_company_creator_is_listed_in_the_directory(inv_client, invitation_app, creator):
    company = _create_company(inv_client, creator["token"], "Directory Creator Co")
    assert creator["id"] in _profiles(invitation_app, company["id"])
