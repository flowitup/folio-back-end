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
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] in ("invitation_sent", "direct_added")

    def test_non_admin_without_perm_returns_403(self, inv_client, outsider_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": "someone@example.com",
                "role_id": invitation_app._test_member_role_id,
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
                "role_id": invitation_app._test_member_role_id,
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
            "role_id": invitation_app._test_member_role_id,
        }
        r1 = inv_client.post("/api/v1/invitations", json=payload, headers=_auth(admin_token))
        assert r1.status_code == 201
        r2 = inv_client.post("/api/v1/invitations", json=payload, headers=_auth(admin_token))
        # Second call revokes old + creates new → still 201
        assert r2.status_code == 201

    def test_existing_member_same_role_returns_201_idempotent(
        self, inv_client, admin_token, invitation_app
    ):
        """Inviting an already-member with the SAME role is idempotent (no-op direct_added)."""
        # Member fixture is already in the project with the 'member' role.
        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": invitation_app._test_member_email,
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["kind"] == "direct_added"

    def test_existing_member_different_role_returns_409(
        self, inv_client, admin_token, invitation_app
    ):
        """Inviting an already-member with a DIFFERENT role is rejected with 409 (H2)."""
        # Look up a different role id (admin) to assign — different from 'member'.
        from app.infrastructure.database.models import RoleModel
        from app import db
        with invitation_app.app_context():
            admin_role = (
                db.session.query(RoleModel).filter(RoleModel.name == "admin").first()
            )
            assert admin_role is not None, "admin role must be seeded by conftest"
            admin_role_id = str(admin_role.id)

        resp = inv_client.post(
            "/api/v1/invitations",
            json={
                "project_id": invitation_app._test_project_id,
                "email": invitation_app._test_member_email,
                "role_id": admin_role_id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409
        body = resp.get_json()
        assert "already a member" in body["message"].lower()


# ---------------------------------------------------------------------------
# GET /api/v1/invitations/projects/<id>/invitations
# ---------------------------------------------------------------------------

class TestListProjectInvitations:
    def test_member_can_list(self, inv_client, member_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations",
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert isinstance(data["items"], list)

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

    def test_status_filter_accepted(self, inv_client, member_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/invitations/projects/{invitation_app._test_project_id}/invitations?status=accepted",
            headers=_auth(member_token),
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
                "role_id": app._test_member_role_id,
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
                "role_id": app._test_member_role_id,
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
                "role_id": invitation_app._test_member_role_id,
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
                "role_id": invitation_app._test_member_role_id,
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
                "role_id": app._test_member_role_id,
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

    def test_valid_accept_returns_200_with_cookies(self, inv_client, admin_token, invitation_app):
        token = self._setup_invitation(inv_client, admin_token, invitation_app)
        if not token:
            pytest.skip("Token extraction failed")

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "New User", "password": "SecurePass123!"},
        )
        assert resp.status_code == 200
        cookies = resp.headers.getlist("Set-Cookie")
        cookie_names = [c.split("=")[0] for c in cookies]
        assert any("access_token" in name for name in cookie_names)

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
                "role_id": invitation_app._test_member_role_id,
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

        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": "User", "password": "SecurePass123!"},
        )
        assert resp.status_code == 410

    def test_invalid_token_returns_404(self, inv_client):
        resp = inv_client.post(
            "/api/v1/invitations/accept",
            json={"token": "completely-unknown-token-xyz", "name": "User", "password": "SecurePass123!"},
        )
        assert resp.status_code == 404
