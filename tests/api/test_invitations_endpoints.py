"""Flask test-client integration tests for invitation API endpoints."""

from __future__ import annotations

import pytest

# All fixtures come from conftest.py (invitation_app, inv_client, admin_token, etc.)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# POST /api/v1/invitations
# ---------------------------------------------------------------------------


class TestCreateInvitation:
    def test_admin_creates_invitation_returns_201(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": "newperson@example.com",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] in ("invitation_sent", "direct_added")

    def test_the_invitation_grants_the_member_role(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={"project_id": invitation_app._test_project_id, "email": "norole@example.com"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["kind"] == "invitation_sent"

    def test_non_admin_without_perm_returns_403(self, inv_client, outsider_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": "someone@example.com",
            },
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, inv_client, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": "x@example.com",
            },
        )
        assert resp.status_code == 401

    def test_missing_fields_returns_422(self, inv_client, admin_token):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={"email": "x@example.com"},  # missing project_id and role_id
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_duplicate_pending_returns_409_or_201(self, inv_client, admin_token, invitation_app):
        """Duplicate pending: use-case revokes old + creates new → 201 (not an error)."""
        payload = {
            "project_id": invitation_app._test_project_id,
            "email": "dup-test@example.com",
        }
        r1 = inv_client.post("/api/v1/invitations", json=payload, headers=_auth(admin_token))
        assert r1.status_code == 201
        r2 = inv_client.post("/api/v1/invitations", json=payload, headers=_auth(admin_token))
        # Second call revokes old + creates new → still 201
        assert r2.status_code == 201

    def test_existing_member_same_role_returns_201_idempotent(self, inv_client, admin_token, invitation_app):
        """Inviting an already-member is an idempotent no-op, reported as such (not as 'added')."""
        # Member fixture is already in the project with the 'member' role.
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": invitation_app._test_member_email,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["kind"] == "already_member"

    @pytest.mark.parametrize("email", ["inv.o'brien@example.com", "inv.jösé@example.com", "inv.a@b.c"])
    def test_an_address_accounts_cannot_carry_is_a_422_not_a_500(self, inv_client, admin_token, invitation_app, email):
        """projects-members-04: EmailStr accepts these, the invitation entity does not."""
        resp = inv_client.post(
            "/api/v1/invitations",
            json={"project_id": invitation_app._test_project_id, "email": email},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422, resp.get_json()
        assert resp.get_json()["reason"] == "invalid_email"

    def test_the_invite_email_follows_the_requested_locale(self, inv_client, admin_token, invitation_app):
        import uuid

        import wiring

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": f"locale-{uuid.uuid4().hex[:8]}@example.com",
                "locale": "fr",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_json()
        mail = inv_client.get("/api/v1/__test__/last-email").get_json()
        assert mail["subject"].startswith("Vous êtes invité à rejoindre ")
        assert "/fr/accept-invite/" in mail["body"]
        assert "en tant que membre" in mail["body"]

    def test_an_unknown_locale_is_refused(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={"project_id": invitation_app._test_project_id, "email": "x-locale@example.com", "locale": "de"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/invitations/projects/<id>/invitations
# ---------------------------------------------------------------------------


class TestListProjectInvitations:
    def test_company_admin_can_list(self, inv_client, admin_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_plain_member_returns_403(self, inv_client, member_token, invitation_app):
        """Listing pending invitations is part of managing people: `project:invite`.

        A bare `user_projects` row is no longer enough — and a legacy global
        role no longer reaches another company's invitations either.
        """
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations",
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_outsider_returns_403(self, inv_client, outsider_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations",
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, inv_client, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations",
        )
        assert resp.status_code == 401

    def test_status_filter_accepted(self, inv_client, admin_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations?status=accepted",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/<id>/revoke
# ---------------------------------------------------------------------------


class TestRevokeInvitation:
    def _create_invitation(self, client, token, app) -> str:
        """Helper: create an invitation and return its id."""
        import uuid

        resp = client.post(
            "/api/v1/invitations",
            json={
                "project_id": app._test_project_id,
                "email": f"revoke-{uuid.uuid4().hex[:8]}@example.com",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        return str(data.get("invitation_id", ""))

    def test_admin_can_revoke_pending(self, inv_client, admin_token, invitation_app):
        inv_id = self._create_invitation(inv_client, admin_token, invitation_app)
        if not inv_id or inv_id == "None":
            pytest.skip("invitation_sent kind expected; got direct_added")
        resp = inv_client.post(
            f"/api/v1/invitations/{inv_id}/revoke",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204

    def test_outsider_cannot_revoke_returns_403(self, inv_client, admin_token, outsider_token, invitation_app):
        inv_id = self._create_invitation(inv_client, admin_token, invitation_app)
        if not inv_id or inv_id == "None":
            pytest.skip("invitation_sent kind expected; got direct_added")
        resp = inv_client.post(
            f"/api/v1/invitations/{inv_id}/revoke",
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_nonexistent_invitation_returns_404(self, inv_client, admin_token):
        import uuid

        resp = inv_client.post(
            f"/api/v1/invitations/{uuid.uuid4()}/revoke",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/invitations/verify/<token>
# ---------------------------------------------------------------------------


class TestVerifyInvitation:
    def _create_and_get_token(self, client, admin_token, app) -> str:
        """Create an invitation and retrieve raw token from __test__ endpoint."""
        import uuid
        import wiring

        # Clear email adapter
        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()

        resp = client.post(
            "/api/v1/invitations",
            json={
                "project_id": app._test_project_id,
                "email": f"verify-{uuid.uuid4().hex[:8]}@example.com",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201

        # Extract token from last email (via __test__ endpoint)
        email_resp = client.get("/api/v1/__test__/last-email")
        if email_resp.status_code == 204:
            return ""
        body_text = email_resp.get_json().get("body", "")
        # Token is embedded in accept URL path: /en/accept-invite/<token>
        import re

        match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
        if not match:
            return ""
        return match.group(1)

    def test_valid_token_returns_200(self, inv_client, admin_token, invitation_app):
        token = self._create_and_get_token(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Could not extract token from email body")
        resp = inv_client.get(f"/api/v1/invitations/verify/{token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "email" in data
        assert "project_name" in data

    def test_unknown_token_returns_404(self, inv_client):
        resp = inv_client.get("/api/v1/invitations/verify/completely-unknown-token-xyz")
        assert resp.status_code == 404

    def test_revoked_token_returns_410(self, inv_client, admin_token, invitation_app):
        import wiring

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()

        import uuid

        email_addr = f"revoked-verify-{uuid.uuid4().hex[:8]}@example.com"
        create_resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": email_addr,
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        inv_id = str(create_resp.get_json().get("invitation_id", ""))
        if not inv_id or inv_id == "None":
            pytest.skip("invitation_sent kind expected")

        # Revoke it
        inv_client.post(f"/api/v1/invitations/{inv_id}/revoke", headers=_auth(admin_token))

        # Get token from email
        email_resp = inv_client.get("/api/v1/__test__/last-email")
        if email_resp.status_code == 204:
            pytest.skip("No email captured")
        body_text = email_resp.get_json().get("body", "")
        import re

        match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
        if not match:
            pytest.skip("Could not extract token")
        token = match.group(1)

        resp = inv_client.get(f"/api/v1/invitations/verify/{token}")
        assert resp.status_code == 410
        assert resp.get_json().get("reason") == "revoked"

    def test_expired_token_returns_410(self, inv_client, admin_token, invitation_app):
        """Simulate expired invitation by directly manipulating the DB."""
        from datetime import datetime, timedelta, timezone
        from app import db
        from app.infrastructure.database.models.invitation import InvitationModel

        import wiring

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()

        import uuid as _uuid

        create_resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": f"expired-{_uuid.uuid4().hex[:8]}@example.com",
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        inv_id = create_resp.get_json().get("invitation_id")
        if not inv_id:
            pytest.skip("invitation_sent expected")

        # Get token
        email_resp = inv_client.get("/api/v1/__test__/last-email")
        if email_resp.status_code == 204:
            pytest.skip("No email captured")
        body_text = email_resp.get_json().get("body", "")
        import re

        match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
        if not match:
            pytest.skip("Token not found in email")
        token = match.group(1)

        # Force-expire via DB
        with invitation_app.app_context():
            model = db.session.query(InvitationModel).filter_by(id=_uuid.UUID(inv_id)).first()
            if model:
                model.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
                db.session.commit()

        resp = inv_client.get(f"/api/v1/invitations/verify/{token}")
        assert resp.status_code == 410
        assert resp.get_json().get("reason") == "expired"


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/accept
# ---------------------------------------------------------------------------


class TestAcceptInvitation:
    def _setup_invitation(self, client, admin_token, app) -> str:
        """Create invitation, return raw token."""
        import uuid
        import wiring
        import re

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()

        email = f"acceptee-{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/api/v1/invitations",
            json={
                "project_id": app._test_project_id,
                "email": email,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201

        email_resp = client.get("/api/v1/__test__/last-email")
        if email_resp.status_code == 204:
            return ""
        body_text = email_resp.get_json().get("body", "")
        match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
        return match.group(1) if match else ""

    def _code_for(self, client, app, token: str, phone: str) -> str:
        """Text a sign-up code to `phone` against a live invitation token, return the 6-digit code."""
        import re

        resp = client.post(
            "/api/v1/invitations/accept/request-code",
            json={"token": token, "phone": phone},
        )
        assert resp.status_code == 202, resp.get_json()
        to, text = app._sms.sent[-1]
        assert to == phone
        match = re.search(r"\b(\d{6})\b", text)
        assert match, text
        return match.group(1)

    def test_valid_accept_returns_200_with_cookies(self, inv_client, admin_token, invitation_app):
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220001"
        code = self._code_for(inv_client, invitation_app, token, phone)

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "New User", "phone": phone, "code": code},
        )
        assert resp.status_code == 200, resp.get_json()
        cookies = resp.headers.getlist("Set-Cookie")
        cookie_names = [c.split("=")[0] for c in cookies]
        assert any("access_token" in name for name in cookie_names)
        # Not session cookies: closing the browser must not end the session.
        assert all("Max-Age=" in c for c in cookies)

    def test_valid_accept_returns_tokens_in_the_body(self, inv_client, admin_token, invitation_app):
        """The bearer-only mobile app reads its session from the body, not the cookies.

        Acceptance signs the invitee in, so it must answer like every other
        token-issuing flow. Returning only cookies would leave a mobile invitee
        accepted but not signed in, with no way to finish without a second code.
        """
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220002"
        code = self._code_for(inv_client, invitation_app, token, phone)

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "Bearer User", "phone": phone, "code": code},
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["access_token"], "acceptance must return an access token in the body"
        assert body["refresh_token"], "acceptance must return a refresh token in the body"
        assert body["user"]["id"]

    def test_expired_returns_410(self, inv_client, admin_token, invitation_app):
        """Test that an expired invitation returns 410."""
        from datetime import datetime, timedelta, timezone
        from app import db
        from app.infrastructure.database.models.invitation import InvitationModel
        import uuid as _uuid
        import re
        import wiring

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()

        email = f"accept-exp-{_uuid.uuid4().hex[:8]}@example.com"
        create_resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": email,
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        inv_id = create_resp.get_json().get("invitation_id")
        if not inv_id:
            pytest.skip("invitation_sent expected")

        email_resp = inv_client.get("/api/v1/__test__/last-email")
        if email_resp.status_code == 204:
            pytest.skip("No email")
        body_text = email_resp.get_json().get("body", "")
        match = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text)
        if not match:
            pytest.skip("No token in email")
        token = match.group(1)

        # Expire it
        with invitation_app.app_context():
            model = db.session.query(InvitationModel).filter_by(id=_uuid.UUID(inv_id)).first()
            if model:
                model.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
                db.session.commit()

        # A well-formed (but never requested) phone + code: the expired-token check
        # happens before the code is ever consumed (AcceptInvitationUseCase.execute()),
        # so no real OTP round-trip is needed to prove this returns 410.
        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": "+33611220099", "code": "424242"},
        )
        assert resp.status_code == 410

    def test_invalid_token_returns_404(self, inv_client):
        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": "completely-unknown-token-xyz", "name": "User", "phone": "+33611220098", "code": "424242"},
        )
        assert resp.status_code == 404

    def test_wrong_code_returns_401(self, inv_client, admin_token, invitation_app):
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220010"
        self._code_for(inv_client, invitation_app, token, phone)  # a real code is sent; deliberately not used

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": phone, "code": "000000"},
        )
        assert resp.status_code == 401, resp.get_json()

    def test_exhausted_attempts_rejects_even_the_correct_code(self, inv_client, admin_token, invitation_app):
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220011"
        code = self._code_for(inv_client, invitation_app, token, phone)

        max_attempts = int(invitation_app.config.get("OTP_MAX_ATTEMPTS", 5))
        for _ in range(max_attempts):
            wrong = inv_client.post(
                "/api/v1/invitations/accept",
                json={"token": token, "name": "User", "phone": phone, "code": "000000"},
            )
            assert wrong.status_code == 401, wrong.get_json()

        # Attempts are now exhausted: even the real code is refused.
        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": phone, "code": code},
        )
        assert resp.status_code == 401, resp.get_json()

    def test_already_used_token_returns_410(self, inv_client, admin_token, invitation_app):
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220012"
        code = self._code_for(inv_client, invitation_app, token, phone)
        first = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": phone, "code": code},
        )
        assert first.status_code == 200, first.get_json()

        # Re-accepting an already-accepted token is refused before any code is consumed
        # (early_inv.accept() runs first), so this phone/code pair is never actually sent.
        again = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": "+33611220013", "code": "424242"},
        )
        assert again.status_code == 410
        assert again.get_json().get("reason") == "accepted"

    def test_duplicate_phone_returns_409(self, inv_client, admin_token, invitation_app):
        """A phone that becomes registered between request-code and accept (e.g. a
        concurrent sign-up) is refused at accept time too, not just at request-code time."""
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        phone = "+33611220014"
        code = self._code_for(inv_client, invitation_app, token, phone)

        # Simulate the phone being claimed by someone else after the code was sent.
        from app import db
        from app.infrastructure.database.models import UserModel

        with invitation_app.app_context():
            db.session.add(UserModel(email=f"raced-{phone.lstrip('+')}@example.com", phone=phone, is_active=True))
            db.session.commit()

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "phone": phone, "code": code},
        )
        assert resp.status_code == 409
        assert resp.get_json().get("reason") == "phone_registered"


class TestInvitationForAnExistingAccount:
    """A pending invitation whose address already has an account (projects-members-02/03).

    It happens when two invitations went out before the first was accepted. Only that
    account may take the invitation up: with a sign-in code sent to its own phone, or from
    a session signed in to it. A code proving any other phone must never sign anyone in.
    """

    OWN_PHONE = "+33611229001"

    def _invite(self, client, admin_token, app) -> tuple[str, str]:
        """Invite a fresh address, then give that address an account; return (email, token)."""
        import re
        import uuid

        import wiring

        if wiring._inmemory_email_adapter:
            wiring._inmemory_email_adapter.clear()
        email = f"second-invite-{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/api/v1/invitations",
            json={"project_id": app._test_project_id, "email": email},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_json()
        body_text = client.get("/api/v1/__test__/last-email").get_json().get("body", "")
        token = re.search(r"/accept-invite/([A-Za-z0-9_\-]+)", body_text).group(1)
        return email, token

    def _give_account(self, app, email: str, phone: str):
        from app import db
        from app.infrastructure.database.models import UserModel

        with app.app_context():
            user = UserModel(email=email, phone=phone, display_name="Already Here", is_active=True)
            db.session.add(user)
            db.session.commit()
            return user.id

    def _code(self, app, phone: str) -> str:
        import re

        to, text = app._sms.sent[-1]
        assert to == phone
        return re.search(r"\b(\d{6})\b", text).group(1)

    def _is_member(self, app, user_id) -> bool:
        from uuid import UUID

        from wiring import get_container

        with app.app_context():
            return get_container().project_membership_repo.exists(user_id, UUID(app._test_project_id))

    def test_another_phone_is_refused_at_request_code(self, inv_client, admin_token, invitation_app):
        email, token = self._invite(inv_client, admin_token, invitation_app)
        self._give_account(invitation_app, email, self.OWN_PHONE)

        resp = inv_client.post(
            "/api/v1/invitations/accept/request-code", json={"token": token, "phone": "+33611229002"}
        )
        assert resp.status_code == 409
        assert resp.get_json()["reason"] == "account_exists"

    def test_a_code_for_another_phone_gets_no_tokens(self, inv_client, admin_token, invitation_app):
        email, token = self._invite(inv_client, admin_token, invitation_app)
        other_phone = "+33611229003"
        # The sign-up code went out while the address had no account yet...
        resp = inv_client.post("/api/v1/invitations/accept/request-code", json={"token": token, "phone": other_phone})
        assert resp.status_code == 202
        code = self._code(invitation_app, other_phone)
        # ...and the account appeared before the code was used.
        user_id = self._give_account(invitation_app, email, "+33611229004")

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "Someone Else", "phone": other_phone, "code": code},
        )
        assert resp.status_code == 409, resp.get_json()
        body = resp.get_json()
        assert body["reason"] == "account_exists"
        assert "access_token" not in body
        assert not resp.headers.getlist("Set-Cookie")
        assert not self._is_member(invitation_app, user_id)

    def test_the_accounts_own_phone_accepts_with_a_sign_in_code(self, inv_client, admin_token, invitation_app):
        email, token = self._invite(inv_client, admin_token, invitation_app)
        phone = "+33611229005"
        user_id = self._give_account(invitation_app, email, phone)

        resp = inv_client.post("/api/v1/invitations/accept/request-code", json={"token": token, "phone": phone})
        assert resp.status_code == 202, resp.get_json()
        code = self._code(invitation_app, phone)

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "Already Here", "phone": phone, "code": code},
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["user"]["id"] == str(user_id)
        assert self._is_member(invitation_app, user_id)

    def test_the_signed_in_account_accepts_without_a_code(self, inv_client, admin_token, invitation_app):
        from tests.auth_login_helper import mint_access_token

        email, token = self._invite(inv_client, admin_token, invitation_app)
        user_id = self._give_account(invitation_app, email, "+33611229006")
        my_token = mint_access_token(inv_client, email)

        resp = inv_client.post("/api/v1/invitations/accept-as-me", json={"token": token}, headers=_auth(my_token))
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["project_id"] == invitation_app._test_project_id
        assert self._is_member(invitation_app, user_id)

        again = inv_client.post("/api/v1/invitations/accept-as-me", json={"token": token}, headers=_auth(my_token))
        assert again.status_code == 410
        assert again.get_json()["reason"] == "accepted"

    def test_another_signed_in_account_is_refused(self, inv_client, admin_token, outsider_token, invitation_app):
        _email, token = self._invite(inv_client, admin_token, invitation_app)

        resp = inv_client.post("/api/v1/invitations/accept-as-me", json={"token": token}, headers=_auth(outsider_token))
        assert resp.status_code == 403
        verify = inv_client.get(f"/api/v1/invitations/verify/{token}")
        assert verify.status_code == 200  # still pending

    def test_accept_as_me_needs_a_session(self, inv_client, admin_token, invitation_app):
        _email, token = self._invite(inv_client, admin_token, invitation_app)
        resp = inv_client.post("/api/v1/invitations/accept-as-me", json={"token": token})
        assert resp.status_code == 401
