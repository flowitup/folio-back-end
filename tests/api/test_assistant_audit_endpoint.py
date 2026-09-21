"""Integration tests for GET /api/v1/assistant/audit (D17 layer 4, phase 03)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from wiring import get_container


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_row(app, *, company_id: str, user_id: str) -> None:
    with app.app_context():
        get_container().assistant_audit_repo.add(
            company_id=uuid.UUID(company_id),
            channel_key=f"company:{company_id}",
            user_id=uuid.UUID(user_id),
            message_id=None,
            intent="find_equipment",
            feature="equipment",
            tools=None,
            outcome="answered",
            refused_reason=None,
            cost_usd=Decimal("0.01"),
            trace_id="trace123",
        )


class TestGetAudit:
    def test_requires_auth(self, inv_client, invitation_app):
        resp = inv_client.get(f"/api/v1/assistant/audit?company_id={invitation_app._test_company_id}")
        assert resp.status_code == 401

    def test_requires_company_id(self, inv_client, admin_token):
        resp = inv_client.get("/api/v1/assistant/audit", headers=_auth(admin_token))
        assert resp.status_code == 422

    def test_member_gets_403(self, inv_client, member_token, invitation_app):
        resp = inv_client.get(
            f"/api/v1/assistant/audit?company_id={invitation_app._test_company_id}", headers=_auth(member_token)
        )
        assert resp.status_code == 403

    def test_company_admin_gets_200_with_rows(self, inv_client, admin_token, invitation_app):
        _seed_row(
            invitation_app, company_id=invitation_app._test_company_id, user_id=invitation_app._test_admin_user_id
        )
        resp = inv_client.get(
            f"/api/v1/assistant/audit?company_id={invitation_app._test_company_id}", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["items"]) >= 1
        row = body["items"][0]
        assert row["intent"] == "find_equipment"
        assert row["outcome"] == "answered"
        assert row["channel_key"] == f"company:{invitation_app._test_company_id}"

    def test_platform_ops_gets_200(self, inv_client, superadmin_token, invitation_app):
        _seed_row(
            invitation_app, company_id=invitation_app._test_company_id, user_id=invitation_app._test_admin_user_id
        )
        resp = inv_client.get(
            f"/api/v1/assistant/audit?company_id={invitation_app._test_company_id}", headers=_auth(superadmin_token)
        )
        assert resp.status_code == 200

    def test_404_when_feature_disabled(self, inv_client, admin_token, invitation_app):
        invitation_app.config["FEATURE_ASSISTANT"] = False
        try:
            resp = inv_client.get(
                f"/api/v1/assistant/audit?company_id={invitation_app._test_company_id}", headers=_auth(admin_token)
            )
            assert resp.status_code == 404
        finally:
            invitation_app.config["FEATURE_ASSISTANT"] = True

    def test_invalid_company_id_422(self, inv_client, admin_token):
        resp = inv_client.get("/api/v1/assistant/audit?company_id=not-a-uuid", headers=_auth(admin_token))
        assert resp.status_code == 422
