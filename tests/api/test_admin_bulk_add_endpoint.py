"""Integration tests for POST /api/v1/admin/users/<id>/memberships (bulk-add)."""

from __future__ import annotations

import uuid


# All fixtures sourced from conftest.py
# (invitation_app, inv_client, superadmin_token, member_token, admin_token, outsider_token)

# NOTE: 429 rate-limit test is skipped — RATELIMIT_ENABLED=False in TestingConfig.


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _bulk_add_url(user_id: str) -> str:
    return f"/api/v1/admin/users/{user_id}/memberships"


# ---------------------------------------------------------------------------
# 200 — valid superadmin request
# ---------------------------------------------------------------------------


class TestBulkAddHappyPath:
    def test_200_valid_request_response_shape(self, inv_client, superadmin_token, invitation_app):
        """Superadmin adding target to a new project returns 200 with results array."""
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "results" in data
        assert isinstance(data["results"], list)
        assert len(data["results"]) == 1
        item = data["results"][0]
        assert "project_id" in item
        assert "project_name" in item
        assert "status" in item
        assert item["status"] == "added"

    def test_200_already_member_same_role_status_in_response(self, inv_client, superadmin_token, invitation_app):
        """target_user is already a member of project (P1) with member_role → same_role status."""
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                # P1 = _test_project_id; target_user is already member with member_role
                "project_ids": [invitation_app._test_project_id],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        item = resp.get_json()["results"][0]
        assert item["status"] == "already_member_same_role"

    def test_200_per_status_discriminator_visible_in_mixed_batch(self, inv_client, superadmin_token, invitation_app):
        """Single request: 1 added + 1 already_member_same_role + 1 project_not_found."""
        nonexistent_pid = str(uuid.uuid4())
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [
                    invitation_app._test_project_3_id,  # new project → added
                    invitation_app._test_project_id,  # already member same role
                    nonexistent_pid,  # not found
                ],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        results = resp.get_json()["results"]
        assert len(results) == 3
        statuses = {r["project_id"]: r["status"] for r in results}
        assert statuses[invitation_app._test_project_3_id] == "added"
        assert statuses[invitation_app._test_project_id] == "already_member_same_role"
        assert statuses[nonexistent_pid] == "project_not_found"


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


class TestBulkAddAuth:
    def test_401_unauthenticated(self, inv_client, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_member_role_id,
            },
        )
        assert resp.status_code == 401

    def test_403_non_superadmin_member(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_403_admin_without_star_perm(self, inv_client, admin_token, invitation_app):
        """admin role has project:invite but not *:* → 403."""
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Input validation (422 / 400)
# ---------------------------------------------------------------------------


class TestBulkAddValidation:
    def test_422_missing_project_ids(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={"role_id": invitation_app._test_member_role_id},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 422

    def test_422_missing_role_id(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={"project_ids": [invitation_app._test_project_2_id]},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 422

    def test_422_wrong_type_project_ids_not_list(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": "not-a-list",
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 422

    def test_422_invalid_uuid_in_project_ids(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": ["not-a-uuid"],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 422

    def test_400_empty_project_ids_pydantic_rejects(self, inv_client, superadmin_token, invitation_app):
        """Pydantic min_length=1 constraint on project_ids → 422 from schema validation."""
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        # Pydantic v2 min_length=1 triggers 422 at schema layer
        assert resp.status_code in (400, 422)

    def test_422_too_many_project_ids_pydantic_rejects(self, inv_client, superadmin_token, invitation_app):
        """Pydantic max_length=50 constraint on project_ids → 422 from schema validation."""
        fifty_one_ids = [str(uuid.uuid4()) for _ in range(51)]
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": fifty_one_ids,
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 404 / 403 domain errors
# ---------------------------------------------------------------------------


class TestBulkAddDomainErrors:
    def test_404_missing_target_user(self, inv_client, superadmin_token, invitation_app):
        nonexistent_user_id = str(uuid.uuid4())
        resp = inv_client.post(
            _bulk_add_url(nonexistent_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_member_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 404

    def test_404_missing_role(self, inv_client, superadmin_token, invitation_app):
        nonexistent_role_id = str(uuid.uuid4())
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": nonexistent_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 404

    def test_403_superadmin_role_assignment_not_allowed(self, inv_client, superadmin_token, invitation_app):
        """Assigning the 'superadmin' role via bulk-add must be rejected with 403."""
        resp = inv_client.post(
            _bulk_add_url(invitation_app._test_target_user_id),
            json={
                "project_ids": [invitation_app._test_project_2_id],
                "role_id": invitation_app._test_superadmin_role_id,
            },
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 500 — unexpected use-case exception
# ---------------------------------------------------------------------------


class TestBulkAddInternalError:
    def test_500_on_unexpected_use_case_exception(self, inv_client, superadmin_token, invitation_app, monkeypatch):
        """An unhandled exception from the use-case must return 500 InternalError."""
        import wiring
        from app.application.admin.bulk_add_existing_user_usecase import BulkAddExistingUserUseCase

        original_uc = wiring.get_container().bulk_add_existing_user_usecase

        broken_uc = BulkAddExistingUserUseCase.__new__(BulkAddExistingUserUseCase)

        def _explode(*args, **kwargs):
            raise RuntimeError("Simulated unexpected failure")

        broken_uc.execute = _explode

        monkeypatch.setattr(wiring.get_container(), "bulk_add_existing_user_usecase", broken_uc)

        try:
            resp = inv_client.post(
                _bulk_add_url(invitation_app._test_target_user_id),
                json={
                    "project_ids": [invitation_app._test_project_2_id],
                    "role_id": invitation_app._test_member_role_id,
                },
                headers=_auth(superadmin_token),
            )
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "bulk_add_existing_user_usecase", original_uc)
