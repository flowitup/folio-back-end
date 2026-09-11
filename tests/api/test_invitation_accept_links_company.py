"""Invitations stay the outsider path (Phase 2 onboarding, slice B): no
company-membership precondition on the acceptor, but once accepted, the new
user becomes a `member` of the invited project's company (derived from the
project, no `company_id` column on invitations — no schema change this slice)
AND is listed in that company's directory, so an admin can assign them.

This module builds its own app through `create_app`, so it exercises the
production DI wiring — the only place the acceptance path's directory repos are
actually connected.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def accept_app():
    from app import create_app, db
    from config import TestingConfig
    from wiring import get_container

    class AcceptTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AcceptTestConfig)
    with test_app.app_context():
        db.create_all()
        db.session.commit()

        # This module exercises the production DI wiring on purpose (see module
        # docstring), so SMS_PROVIDER defaults to LoggingSmsSender, which only
        # logs. Intercept the SAME instance the OTP use cases already hold a
        # reference to (they captured it at construction time, so swapping
        # container.sms_sender afterwards would not reach them) to recover the
        # real 6-digit code sent to each acceptor's phone.
        sent: list[tuple[str, str]] = []
        get_container().sms_sender.send = lambda to, text: sent.append((to, text))
        test_app._sms_sent = sent

        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def accept_client(accept_app):
    return accept_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


def _code_from_sms(app) -> str:
    to, text = app._sms_sent[-1]
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match.group(1)


def _make_admin_with_company_project(app):
    """Create a company admin, their company and a project. Returns `(email, company_id, project_id)`.

    The app fixture is module-scoped, so each call needs its own admin email.
    """
    from app import db

    now = datetime.now(timezone.utc)
    admin_email = f"ial_admin_{uuid.uuid4().hex[:8]}@test.com"
    with app.app_context():
        admin = UserModel(
            id=uuid.uuid4(),
            email=admin_email,
            is_active=True,
        )
        db.session.add(admin)
        db.session.flush()
        company = CompanyModel(
            id=uuid.uuid4(), legal_name="IAL Co", address="1 rue", created_by=admin.id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin.id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        project = ProjectModel(id=uuid.uuid4(), name="IAL Project", owner_id=admin.id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return admin_email, company.id, project.id


def _invite_and_accept(client, app, admin_token, project_id) -> str:
    """Run the whole outsider path; returns the acceptor's email."""
    invitee_email = f"ial-invitee-{uuid.uuid4().hex[:8]}@example.com"
    # A fresh French mobile number per call — users.phone is unique, and this
    # helper runs more than once against the same module-scoped database.
    invitee_phone = f"+336{uuid.uuid4().int % 10**8:08d}"
    create_resp = client.post(
        "/api/v1/invitations",
        json={"project_id": str(project_id), "email": invitee_email},
        headers=_auth(admin_token),
    )
    assert create_resp.status_code == 201, create_resp.get_data(as_text=True)

    email_resp = client.get("/api/v1/__test__/last-email")
    if email_resp.status_code == 204:
        pytest.skip("Test-only email capture endpoint unavailable")
    body_text = email_resp.get_json().get("body", "")
    match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
    if not match:
        pytest.skip("Token extraction failed")
    token = match.group(1)

    code_resp = client.post(
        "/api/v1/invitations/accept/request-code",
        json={"token": token, "phone": invitee_phone},
    )
    assert code_resp.status_code == 202, code_resp.get_data(as_text=True)
    code = _code_from_sms(app)

    accept_resp = client.post(
        "/api/v1/invitations/accept",
        json={"token": token, "name": "Invited Outsider", "phone": invitee_phone, "code": code},
    )
    assert accept_resp.status_code == 200, accept_resp.get_data(as_text=True)
    return invitee_email


class TestAcceptInvitationLinksCompany:
    def test_accepted_invitation_attaches_acceptor_to_project_company(self, accept_client, accept_app):
        admin_email, company_id, project_id = _make_admin_with_company_project(accept_app)
        admin_token = _login(accept_client, admin_email)

        invitee_email = _invite_and_accept(accept_client, accept_app, admin_token, project_id)

        from app import db

        with accept_app.app_context():
            new_user = db.session.query(UserModel).filter_by(email=invitee_email).first()
            assert new_user is not None
            access = db.session.get(UserCompanyAccessModel, (new_user.id, company_id))
            assert access is not None, "acceptor must become a member of the project's company"
            assert access.role == "member"
            assert access.is_primary is True

    def test_accepted_invitation_lists_the_acceptor_in_the_company_directory(self, accept_client, accept_app):
        """Attached ⇒ listed: without a profile the assign-member picker cannot see them."""
        admin_email, company_id, project_id = _make_admin_with_company_project(accept_app)
        admin_token = _login(accept_client, admin_email)

        invitee_email = _invite_and_accept(accept_client, accept_app, admin_token, project_id)

        from app import db
        from app.infrastructure.database.models.company_person import CompanyPersonModel
        from app.infrastructure.database.models.person import PersonModel

        with accept_app.app_context():
            new_user = db.session.query(UserModel).filter_by(email=invitee_email).first()
            person = db.session.query(PersonModel).filter_by(user_id=new_user.id).first()
            assert person is not None, "acceptance must create the global person identity"
            profile = db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=person.id).first()
            assert profile is not None, "acceptor must appear in the company directory"
            assert profile.is_active is True
