"""Integration tests for GET /api/v1/admin/users?search=<q>&limit=<n> (user search)."""

from __future__ import annotations


# All fixtures sourced from conftest.py
# (invitation_app, inv_client, superadmin_token, member_token, admin_token)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_SEARCH_URL = "/api/v1/admin/users"


# ---------------------------------------------------------------------------
# 200 — results returned
# ---------------------------------------------------------------------------


class TestUserSearchResults:
    def test_200_search_by_email_prefix_returns_match(self, inv_client, superadmin_token, invitation_app):
        """Searching by the seeded target user's email prefix returns at least one result."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target@invite-test"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "count" in data
        assert data["count"] >= 1
        emails = [item["email"] for item in data["items"]]
        assert any("target@invite-test" in e for e in emails)

    def test_200_search_by_display_name_returns_match(self, inv_client, superadmin_token, invitation_app):
        """Searching by display_name substring 'Target User' returns the seeded target user."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "Target User"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # At least target_user should appear
        assert data["count"] >= 1

    def test_200_no_match_returns_empty_items(self, inv_client, superadmin_token):
        """Query that matches nothing returns empty items list with count 0."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "zzz-no-match-xyz-9999"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["count"] == 0

    def test_200_empty_search_returns_empty_items(self, inv_client, superadmin_token):
        """Empty search param returns empty items array (early return in route)."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": ""},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["count"] == 0

    def test_200_no_search_param_returns_empty_items(self, inv_client, superadmin_token):
        """Omitting search param entirely returns empty items array."""
        resp = inv_client.get(_SEARCH_URL, headers=_auth(superadmin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


class TestUserSearchAuth:
    def test_401_unauthenticated(self, inv_client):
        resp = inv_client.get(_SEARCH_URL, query_string={"search": "target"})
        assert resp.status_code == 401

    def test_403_member_without_star_perm(self, inv_client, member_token):
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_403_admin_without_star_perm(self, inv_client, admin_token):
        """admin role has project:invite but not *:* → 403."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Limit clamping
# ---------------------------------------------------------------------------


class TestUserSearchLimit:
    def test_limit_over_20_clamped_to_20(self, inv_client, superadmin_token):
        """limit=100 should be silently clamped to 20 (no error)."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "invite-test", "limit": 100},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # count reflects actual matches; items must not exceed 20
        assert len(data["items"]) <= 20

    def test_limit_zero_clamped_to_1(self, inv_client, superadmin_token):
        """limit=0 clamped to 1 (min clamp)."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "invite-test", "limit": 0},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        # Should return at most 1 result (no error)
        assert len(resp.get_json()["items"]) <= 1

    def test_limit_negative_clamped_to_1(self, inv_client, superadmin_token):
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "invite-test", "limit": -5},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["items"]) <= 1

    def test_limit_invalid_string_defaults_to_20(self, inv_client, superadmin_token):
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "invite-test", "limit": "abc"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["items"]) <= 20


# ---------------------------------------------------------------------------
# Query length guard
# ---------------------------------------------------------------------------


class TestUserSearchQueryGuard:
    def test_400_query_over_100_chars(self, inv_client, superadmin_token):
        """Query string longer than 100 characters is rejected with 400."""
        long_query = "a" * 101
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": long_query},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 400

    def test_200_query_exactly_100_chars(self, inv_client, superadmin_token):
        """Query of exactly 100 characters is accepted (boundary)."""
        boundary_query = "a" * 100
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": boundary_query},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response shape — sensitive fields excluded
# ---------------------------------------------------------------------------


class TestUserSearchResponseShape:
    def test_response_excludes_password_hash(self, inv_client, superadmin_token):
        """password_hash must never appear in search results."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target@invite-test"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        for item in resp.get_json()["items"]:
            assert "password_hash" not in item

    def test_response_excludes_roles(self, inv_client, superadmin_token):
        """roles field must not be in search results."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target@invite-test"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        for item in resp.get_json()["items"]:
            assert "roles" not in item

    def test_response_excludes_permissions(self, inv_client, superadmin_token):
        """permissions field must not be in search results."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target@invite-test"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        for item in resp.get_json()["items"]:
            assert "permissions" not in item

    def test_response_items_have_expected_keys(self, inv_client, superadmin_token):
        """Each item must contain id, email, display_name — nothing else sensitive."""
        resp = inv_client.get(
            _SEARCH_URL,
            query_string={"search": "target@invite-test"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) >= 1
        for item in items:
            assert "id" in item
            assert "email" in item
            assert "display_name" in item
