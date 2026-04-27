"""Security test: no public self-registration endpoints exist."""

from __future__ import annotations

# Uses the invitation_app + inv_client fixtures from conftest.py


class TestNoPublicSignup:
    def test_auth_register_returns_404(self, inv_client):
        resp = inv_client.post(
            "/api/v1/auth/register",
            json={"email": "hacker@example.com", "password": "password123"},
        )
        assert resp.status_code == 404

    def test_users_post_blocked(self, inv_client):
        # 404 (route absent) or 405 (route exists for other methods only) both prove
        # "POST /users cannot create accounts"; either is acceptable.
        resp = inv_client.post(
            "/api/v1/users",
            json={"email": "hacker@example.com", "password": "password123"},
        )
        assert resp.status_code in (404, 405)

    def test_signup_returns_404(self, inv_client):
        resp = inv_client.post(
            "/api/v1/signup",
            json={"email": "hacker@example.com", "password": "password123"},
        )
        assert resp.status_code == 404
