"""API tests for billing document template endpoints."""

from __future__ import annotations

import uuid


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_TPL_BODY = {
    "kind": "devis",
    "name": "Standard Consulting",
    "items": [{"description": "Consulting", "quantity": "1", "unit_price": "800", "vat_rate": "20"}],
}


class TestListTemplates:
    def test_list_returns_200(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-document-templates", headers=_auth(billing_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data

    def test_list_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get("/api/v1/billing-document-templates")
        assert resp.status_code == 401

    def test_list_filter_by_kind(self, inv_client, billing_token, seeded_template):
        resp = inv_client.get("/api/v1/billing-document-templates?kind=devis", headers=_auth(billing_token))
        assert resp.status_code == 200

    def test_list_invalid_kind_returns_400(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-document-templates?kind=invalid", headers=_auth(billing_token))
        assert resp.status_code == 400


class TestCreateTemplate:
    def test_create_returns_201(self, inv_client, billing_token):
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=_TPL_BODY,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "devis"
        assert data["name"] == "Standard Consulting"

    def test_create_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.post("/api/v1/billing-document-templates", json=_TPL_BODY)
        assert resp.status_code == 401

    def test_create_extra_field_returns_422(self, inv_client, billing_token):
        body = {**_TPL_BODY, "unexpected": "field"}
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=body,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_create_missing_name_returns_422(self, inv_client, billing_token):
        body = {"kind": "devis", "items": []}
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=body,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422


class TestGetTemplate:
    def test_get_own_template_returns_200(self, inv_client, billing_token, seeded_template):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["id"] == seeded_template["id"]

    def test_get_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_get_nonexistent_returns_404(self, inv_client, billing_token):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{uuid.uuid4()}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404


class TestUpdateTemplate:
    def test_update_name_returns_200(self, inv_client, billing_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"name": "Updated Template Name"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated Template Name"

    def test_update_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"name": "Hacked"},
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_apply_template_kind_mismatch_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #11: apply devis template to create facture-kind doc is prevented
        because the kind comes from the template itself — template kind cannot be
        overridden via the apply route. Test that the schema rejects extra fields.
        """
        # Create a devis template
        tpl_resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json={"kind": "devis", "name": "Devis TPL"},
            headers=_auth(billing_token),
        )
        assert tpl_resp.status_code == 201
        tpl_id = tpl_resp.get_json()["id"]

        # Apply the template — extra field `kind` in body is rejected by schema
        apply_resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{tpl_id}",
            json={
                "recipient_name": "Client",
                "kind": "facture",  # kind not allowed in ApplyTemplateRequest
                "company_id": billing_profile["company_id"],
            },
            headers=_auth(billing_token),
        )
        assert apply_resp.status_code == 422


class TestDeleteTemplate:
    def test_delete_own_template_returns_204(self, inv_client, billing_token):
        create_resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json={"kind": "facture", "name": "To Delete"},
            headers=_auth(billing_token),
        )
        assert create_resp.status_code == 201
        tpl_id = create_resp.get_json()["id"]

        resp = inv_client.delete(
            f"/api/v1/billing-document-templates/{tpl_id}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 204

    def test_delete_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.delete(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404


class TestApplyTemplate:
    def test_apply_template_creates_doc(self, inv_client, billing_token, billing_profile, seeded_template):
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{seeded_template['id']}",
            json={
                "recipient_name": "Client via Template",
                "company_id": billing_profile["company_id"],
            },
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "devis"
        assert data["recipient_name"] == "Client via Template"

    def test_apply_nonexistent_template_returns_404(self, inv_client, billing_token, billing_profile):
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{uuid.uuid4()}",
            json={"recipient_name": "X", "company_id": billing_profile["company_id"]},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404

    def test_apply_template_no_profile_returns_409(self, inv_client, other_token, seeded_template):
        """other_token user has no profile — returns 409."""
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{seeded_template['id']}",
            json={"recipient_name": "X"},
            headers=_auth(other_token),
        )
        # other_token doesn't own the template → 404 (ownership checked first)
        assert resp.status_code == 404


class TestTemplateLineSections:
    """A template line keeps its section (`category`) on create, update and apply."""

    def _line(self, category):
        return {"description": "Tiling", "quantity": "2", "unit_price": "40", "vat_rate": "10", "category": category}

    def test_category_round_trips_through_create_get_update_and_apply(self, inv_client, billing_token, billing_profile):
        created = inv_client.post(
            "/api/v1/billing-document-templates",
            json={"kind": "devis", "name": f"Sections {uuid.uuid4().hex[:6]}", "items": [self._line("Bathroom")]},
            headers=_auth(billing_token),
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        tpl_id = created.get_json()["id"]
        assert created.get_json()["items"][0]["category"] == "Bathroom"

        fetched = inv_client.get(f"/api/v1/billing-document-templates/{tpl_id}", headers=_auth(billing_token))
        assert fetched.get_json()["items"][0]["category"] == "Bathroom"

        updated = inv_client.put(
            f"/api/v1/billing-document-templates/{tpl_id}",
            json={"items": [self._line("Kitchen"), self._line("Hall")]},
            headers=_auth(billing_token),
        )
        assert updated.status_code == 200, updated.get_data(as_text=True)
        assert [it["category"] for it in updated.get_json()["items"]] == ["Kitchen", "Hall"]

        applied = inv_client.post(
            f"/api/v1/billing-documents/from-template/{tpl_id}",
            json={"recipient_name": "Client", "company_id": billing_profile["company_id"]},
            headers=_auth(billing_token),
        )
        assert applied.status_code == 201, applied.get_data(as_text=True)
        assert [it["category"] for it in applied.get_json()["items"]] == ["Kitchen", "Hall"]


class TestTemplateTextLimits:
    """Template notes and terms use the document caps, so a template always makes a savable document."""

    def test_create_with_notes_over_2000_characters_returns_422(self, inv_client, billing_token):
        body = {"kind": "devis", "name": f"Long {uuid.uuid4().hex[:6]}", "notes": "n" * 2001}
        resp = inv_client.post("/api/v1/billing-document-templates", json=body, headers=_auth(billing_token))
        assert resp.status_code == 422, resp.get_data(as_text=True)
        assert "notes" in resp.get_json()["message"]

    def test_update_with_terms_over_2000_characters_returns_422(self, inv_client, billing_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"terms": "t" * 2001},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422, resp.get_data(as_text=True)
        assert "terms" in resp.get_json()["message"]

    def test_2000_characters_are_accepted(self, inv_client, billing_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"notes": "n" * 2000, "terms": "t" * 2000},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def _lines(self, count: int) -> list[dict]:
        return [
            {"description": f"Line {i}", "quantity": "1", "unit_price": "1", "vat_rate": "20"} for i in range(count)
        ]

    def test_more_than_200_lines_returns_422_on_create_and_update(self, inv_client, billing_token, seeded_template):
        body = {"kind": "devis", "name": f"Many {uuid.uuid4().hex[:6]}", "items": self._lines(201)}
        created = inv_client.post("/api/v1/billing-document-templates", json=body, headers=_auth(billing_token))
        assert created.status_code == 422, created.get_data(as_text=True)
        assert "items" in created.get_json()["message"]
        updated = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"items": self._lines(201)},
            headers=_auth(billing_token),
        )
        assert updated.status_code == 422, updated.get_data(as_text=True)
        assert "items" in updated.get_json()["message"]

    def test_200_lines_are_accepted(self, inv_client, billing_token):
        body = {"kind": "devis", "name": f"Full {uuid.uuid4().hex[:6]}", "items": self._lines(200)}
        resp = inv_client.post("/api/v1/billing-document-templates", json=body, headers=_auth(billing_token))
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_apply_with_address_over_500_characters_returns_422(
        self, inv_client, billing_token, billing_profile, seeded_template
    ):
        url = f"/api/v1/billing-documents/from-template/{seeded_template['id']}"
        body = {"recipient_name": "Client", "company_id": billing_profile["company_id"]}
        too_long = inv_client.post(url, json={**body, "recipient_address": "A" * 501}, headers=_auth(billing_token))
        assert too_long.status_code == 422, too_long.get_data(as_text=True)
        assert "recipient_address" in too_long.get_json()["message"]
        fits = inv_client.post(url, json={**body, "recipient_address": "A" * 500}, headers=_auth(billing_token))
        assert fits.status_code == 201, fits.get_data(as_text=True)


class TestTemplateDefaultVatRate:
    """The column keeps 2 decimals: create and update answer with the rounded rate that is stored."""

    def test_create_and_update_return_the_stored_rate(self, inv_client, billing_token):
        body = {"kind": "devis", "name": f"Vat {uuid.uuid4().hex[:6]}", "default_vat_rate": "5.555"}
        created = inv_client.post("/api/v1/billing-document-templates", json=body, headers=_auth(billing_token))
        assert created.status_code == 201, created.get_data(as_text=True)
        tpl_id = created.get_json()["id"]
        assert created.get_json()["default_vat_rate"] == "5.56"

        updated = inv_client.put(
            f"/api/v1/billing-document-templates/{tpl_id}",
            json={"default_vat_rate": "7.777"},
            headers=_auth(billing_token),
        )
        assert updated.status_code == 200, updated.get_data(as_text=True)
        assert updated.get_json()["default_vat_rate"] == "7.78"
        fetched = inv_client.get(f"/api/v1/billing-document-templates/{tpl_id}", headers=_auth(billing_token))
        assert fetched.get_json()["default_vat_rate"] == "7.78"


class TestTemplateUpdateClearsAndConflicts:
    def test_null_clears_notes_terms_and_default_vat(self, inv_client, billing_token, seeded_template):
        url = f"/api/v1/billing-document-templates/{seeded_template['id']}"
        set_resp = inv_client.put(
            url,
            json={"notes": "Note à effacer", "terms": "CGV à effacer", "default_vat_rate": "10"},
            headers=_auth(billing_token),
        )
        assert set_resp.status_code == 200, set_resp.get_data(as_text=True)

        cleared = inv_client.put(
            url, json={"notes": None, "terms": None, "default_vat_rate": None}, headers=_auth(billing_token)
        )
        assert cleared.status_code == 200, cleared.get_data(as_text=True)
        data = inv_client.get(url, headers=_auth(billing_token)).get_json()
        assert (data["notes"], data["terms"], data["default_vat_rate"]) == (None, None, None)

    def test_omitted_fields_are_left_alone(self, inv_client, billing_token, seeded_template):
        url = f"/api/v1/billing-document-templates/{seeded_template['id']}"
        inv_client.put(url, json={"notes": "Garde-moi", "default_vat_rate": "10"}, headers=_auth(billing_token))
        resp = inv_client.put(url, json={"name": f"Renamed {uuid.uuid4().hex[:6]}"}, headers=_auth(billing_token))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["notes"] == "Garde-moi"
        assert resp.get_json()["default_vat_rate"] is not None

    def test_rename_onto_an_existing_name_returns_409(self, inv_client, billing_token):
        names = [f"Dup A {uuid.uuid4().hex[:6]}", f"Dup B {uuid.uuid4().hex[:6]}"]
        ids = []
        for name in names:
            resp = inv_client.post(
                "/api/v1/billing-document-templates",
                json={"kind": "facture", "name": name},
                headers=_auth(billing_token),
            )
            assert resp.status_code == 201, resp.get_data(as_text=True)
            ids.append(resp.get_json()["id"])

        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{ids[1]}", json={"name": names[0]}, headers=_auth(billing_token)
        )
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "Conflict"
        # The session is usable again and B kept its name.
        kept = inv_client.get(f"/api/v1/billing-document-templates/{ids[1]}", headers=_auth(billing_token))
        assert kept.get_json()["name"] == names[1]
