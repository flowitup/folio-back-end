"""Integration tests for the project-scoped chiffrage endpoints.

Authorization split used throughout:
  writer_token -> holds ``*:*``, may write
  reader_token     -> holds ``project:read`` only, read-only
"""

from __future__ import annotations

import uuid

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _base(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/chiffrage"


@pytest.fixture(scope="module")
def chiffrage_world(invitation_app):
    """A project owned by a writer, plus a read-only member — built via the ORM.

    The shared invitation fixture inserts memberships with raw SQL, which the
    Project relationship never picks up (project.user_ids comes back empty), so
    require_project_access denies even legitimate members — the sibling invoices
    endpoints return 403 for that same user. Building this world through the ORM
    keeps the suite testing the authorization rules rather than that artefact.
    """
    from app import db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel

    hasher = Argon2PasswordHasher()
    with invitation_app.app_context():

        def perm(name: str) -> PermissionModel:
            existing = db.session.query(PermissionModel).filter_by(name=name).one_or_none()
            if existing:
                return existing
            resource, action = name.split(":", 1)
            created = PermissionModel(name=name, resource=resource, action=action)
            db.session.add(created)
            return created

        writer_role = RoleModel(name="chiffrage-writer", description="Chiffrage writer")
        reader_role = RoleModel(name="chiffrage-reader", description="Chiffrage reader")
        # Add the roles before wiring permissions so the association writes are
        # not attempted against detached objects during autoflush.
        db.session.add_all([writer_role, reader_role])
        writer_role.permissions.append(perm("project:read"))
        writer_role.permissions.append(perm("project:manage_invoices"))

        reader_role.permissions.append(perm("project:read"))

        writer = UserModel(email="chiffrage-writer@test.com", password_hash=hasher.hash("Writer1234!"), is_active=True)
        writer.roles.append(writer_role)
        reader = UserModel(email="chiffrage-reader@test.com", password_hash=hasher.hash("Reader1234!"), is_active=True)
        reader.roles.append(reader_role)
        db.session.add_all([writer, reader])
        db.session.flush()

        project = ProjectModel(name="Chiffrage Test Project", owner_id=writer.id)
        project.users.append(reader)  # ORM append -> project.user_ids is populated
        other = ProjectModel(name="Chiffrage Other Project", owner_id=writer.id)
        db.session.add_all([project, other])
        db.session.commit()

        return {
            "project_id": str(project.id),
            "other_project_id": str(other.id),
            "writer": ("chiffrage-writer@test.com", "Writer1234!"),
            "reader": ("chiffrage-reader@test.com", "Reader1234!"),
        }


def _token(client, credentials) -> str:
    email, password = credentials
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def writer_token(inv_client, chiffrage_world) -> str:
    return _token(inv_client, chiffrage_world["writer"])


@pytest.fixture
def reader_token(inv_client, chiffrage_world) -> str:
    """Project member holding project:read only — may read, may not write."""
    return _token(inv_client, chiffrage_world["reader"])


@pytest.fixture
def project_id(chiffrage_world) -> str:
    return chiffrage_world["project_id"]


@pytest.fixture
def poste(inv_client, writer_token, project_id) -> dict:
    resp = inv_client.post(
        f"{_base(project_id)}/postes",
        json={"name": "Lumière"},
        headers=_auth(writer_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


@pytest.fixture
def article(inv_client, writer_token, project_id, poste) -> dict:
    resp = inv_client.post(
        f"{_base(project_id)}/postes/{poste['id']}/articles",
        json={"name": "Spot encastré", "quantity": "12", "unit": "u"},
        headers=_auth(writer_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _add_quote(client, token, project_id, article_id, supplier, price, tva="20"):
    resp = client.post(
        f"{_base(project_id)}/articles/{article_id}/quotes",
        json={"supplier_name": supplier, "unit_price_ht": price, "tva_rate": tva},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _article_in_tree(tree: dict, article_id: str) -> dict:
    for p in tree["postes"]:
        for a in p["articles"]:
            if a["id"] == article_id:
                return a
    raise AssertionError(f"article {article_id} missing from tree")


class TestTreeAndTotals:
    def test_empty_project_returns_zero_totals(self, inv_client, reader_token, project_id):
        resp = inv_client.get(_base(project_id), headers=_auth(reader_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_ht"] == 0.0
        assert data["total_ttc"] == 0.0
        assert data["unpriced_article_count"] == 0

    def test_article_without_quote_is_counted_unpriced_not_free(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        found = _article_in_tree(tree, article["id"])
        assert found["effective_source"] == "none"
        assert found["total_ht"] == 0.0
        assert tree["unpriced_article_count"] >= 1

    def test_cheapest_quote_drives_the_total_until_one_is_retained(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        _add_quote(inv_client, writer_token, project_id, article["id"], "Leroy Merlin", "10.75")
        _add_quote(inv_client, writer_token, project_id, article["id"], "Point P", "12.40")

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        found = _article_in_tree(tree, article["id"])
        assert found["effective_source"] == "cheapest"
        assert found["total_ht"] == 129.00  # 12 x 10.75
        assert found["total_ttc"] == 154.80

    def test_retained_quote_overrides_a_cheaper_one(self, inv_client, writer_token, reader_token, project_id, article):
        _add_quote(inv_client, writer_token, project_id, article["id"], "Leroy Merlin", "10.75")
        dearer = _add_quote(inv_client, writer_token, project_id, article["id"], "Rexel", "11.90")

        resp = inv_client.post(f"{_base(project_id)}/quotes/{dearer['id']}/select", headers=_auth(writer_token))
        assert resp.status_code == 200

        found = _article_in_tree(
            inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json(), article["id"]
        )
        assert found["effective_source"] == "selected"
        assert found["total_ht"] == 142.80  # 12 x 11.90
        assert sum(1 for q in found["quotes"] if q["is_selected"]) == 1

    def test_selecting_another_quote_clears_the_previous_selection(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        first = _add_quote(inv_client, writer_token, project_id, article["id"], "A", "10.00")
        second = _add_quote(inv_client, writer_token, project_id, article["id"], "B", "11.00")

        inv_client.post(f"{_base(project_id)}/quotes/{first['id']}/select", headers=_auth(writer_token))
        inv_client.post(f"{_base(project_id)}/quotes/{second['id']}/select", headers=_auth(writer_token))

        found = _article_in_tree(
            inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json(), article["id"]
        )
        selected = [q["id"] for q in found["quotes"] if q["is_selected"]]
        assert selected == [second["id"]]

    def test_deleting_the_retained_quote_falls_back_to_cheapest(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        cheap = _add_quote(inv_client, writer_token, project_id, article["id"], "Cheap", "10.00")
        dear = _add_quote(inv_client, writer_token, project_id, article["id"], "Dear", "11.00")
        inv_client.post(f"{_base(project_id)}/quotes/{dear['id']}/select", headers=_auth(writer_token))

        resp = inv_client.delete(f"{_base(project_id)}/quotes/{dear['id']}", headers=_auth(writer_token))
        assert resp.status_code == 204

        found = _article_in_tree(
            inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json(), article["id"]
        )
        assert found["effective_source"] == "cheapest"
        assert found["effective_quote_id"] == cheap["id"]

    def test_displayed_lines_sum_exactly_to_the_displayed_totals(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        """A one-cent gap between rows and the total is an accounting bug."""
        specs = [("A", "3", "33.33", "10"), ("B", "7", "12.34", "20"), ("C", "11", "5.55", "5.5")]
        for name, qty, price, tva in specs:
            art = inv_client.post(
                f"{_base(project_id)}/postes/{poste['id']}/articles",
                json={"name": name, "quantity": qty, "unit": "u"},
                headers=_auth(writer_token),
            ).get_json()
            _add_quote(inv_client, writer_token, project_id, art["id"], "S", price, tva)

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        for p in tree["postes"]:
            assert round(sum(a["total_ht"] for a in p["articles"]), 2) == p["subtotal_ht"]
            assert round(sum(a["total_ttc"] for a in p["articles"]), 2) == p["subtotal_ttc"]
        assert round(sum(p["subtotal_ht"] for p in tree["postes"]), 2) == tree["total_ht"]
        assert round(sum(p["subtotal_ttc"] for p in tree["postes"]), 2) == tree["total_ttc"]


class TestUnits:
    def test_presets_are_listed_and_flagged(self, inv_client, reader_token, project_id):
        resp = inv_client.get(f"{_base(project_id)}/units", headers=_auth(reader_token))
        assert resp.status_code == 200
        units = resp.get_json()
        symbols = [u["symbol"] for u in units]
        assert "u" in symbols and "m²" in symbols
        assert all(u["is_preset"] and u["id"] is None for u in units if u["symbol"] in {"u", "m²"})

    def test_custom_unit_is_added_and_listed(self, inv_client, writer_token, reader_token, project_id):
        resp = inv_client.post(f"{_base(project_id)}/units", json={"symbol": "sac 25kg"}, headers=_auth(writer_token))
        assert resp.status_code == 201
        assert resp.get_json()["is_preset"] is False

        units = inv_client.get(f"{_base(project_id)}/units", headers=_auth(reader_token)).get_json()
        custom = [u for u in units if u["symbol"] == "sac 25kg"]
        assert len(custom) == 1 and custom[0]["id"] is not None

    def test_duplicate_custom_unit_is_rejected(self, inv_client, writer_token, project_id):
        inv_client.post(f"{_base(project_id)}/units", json={"symbol": "botte"}, headers=_auth(writer_token))
        resp = inv_client.post(f"{_base(project_id)}/units", json={"symbol": "botte"}, headers=_auth(writer_token))
        assert resp.status_code == 409

    def test_symbol_colliding_with_a_preset_is_rejected(self, inv_client, writer_token, project_id):
        """Allowing it would render the same symbol twice in the merged select."""
        resp = inv_client.post(f"{_base(project_id)}/units", json={"symbol": "u"}, headers=_auth(writer_token))
        assert resp.status_code == 409

    def test_article_with_an_unknown_unit_is_rejected(self, inv_client, writer_token, project_id, poste):
        """This is what makes the front-end dropdown authoritative."""
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Bad", "quantity": "1", "unit": "parsec"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_deleting_a_custom_unit_leaves_articles_using_it_intact(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        created = inv_client.post(
            f"{_base(project_id)}/units", json={"symbol": "palette"}, headers=_auth(writer_token)
        ).get_json()
        art = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Carrelage", "quantity": "2", "unit": "palette"},
            headers=_auth(writer_token),
        ).get_json()

        resp = inv_client.delete(f"{_base(project_id)}/units/{created['id']}", headers=_auth(writer_token))
        assert resp.status_code == 204

        found = _article_in_tree(inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json(), art["id"])
        assert found["unit"] == "palette"


class TestReorder:
    def _articles(self, client, token, project_id, poste_id):
        tree = client.get(_base(project_id), headers=_auth(token)).get_json()
        poste = next(p for p in tree["postes"] if p["id"] == poste_id)
        return [a["name"] for a in poste["articles"]]

    def test_drop_between_two_neighbours_lands_strictly_between_them(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        made = []
        for name in ("A", "B", "C"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes/{poste['id']}/articles",
                    json={"name": name, "quantity": "1", "unit": "u"},
                    headers=_auth(writer_token),
                ).get_json()
            )
        a, b, c = made

        resp = inv_client.post(
            f"{_base(project_id)}/articles/{c['id']}/reorder",
            json={"before_id": a["id"], "after_id": b["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert a["position"] < resp.get_json()["position"] < b["position"]
        assert self._articles(inv_client, reader_token, project_id, poste["id"]) == ["A", "C", "B"]

    def test_drop_at_head_moves_before_everything(self, inv_client, writer_token, reader_token, project_id, poste):
        made = []
        for name in ("A", "B"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes/{poste['id']}/articles",
                    json={"name": name, "quantity": "1", "unit": "u"},
                    headers=_auth(writer_token),
                ).get_json()
            )
        inv_client.post(
            f"{_base(project_id)}/articles/{made[1]['id']}/reorder",
            json={"after_id": made[0]["id"]},
            headers=_auth(writer_token),
        )
        assert self._articles(inv_client, reader_token, project_id, poste["id"]) == ["B", "A"]

    def test_article_cannot_be_reordered_into_another_poste(self, inv_client, writer_token, project_id, poste):
        other = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Plomberie"}, headers=_auth(writer_token)
        ).get_json()
        here = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Ici", "quantity": "1", "unit": "u"},
            headers=_auth(writer_token),
        ).get_json()
        there = inv_client.post(
            f"{_base(project_id)}/postes/{other['id']}/articles",
            json={"name": "Ailleurs", "quantity": "1", "unit": "u"},
            headers=_auth(writer_token),
        ).get_json()

        resp = inv_client.post(
            f"{_base(project_id)}/articles/{here['id']}/reorder",
            json={"before_id": there["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400


class TestPatchDoesNotDropFields:
    def test_patching_only_the_note_keeps_every_other_field(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        resp = inv_client.patch(
            f"{_base(project_id)}/articles/{article['id']}",
            json={"note": "prévoir 2 de rab"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["name"] == "Spot encastré"
        assert body["quantity"] == 12.0
        assert body["unit"] == "u"
        assert body["note"] == "prévoir 2 de rab"

    def test_patching_only_the_price_keeps_the_supplier(self, inv_client, writer_token, project_id, article):
        quote = _add_quote(inv_client, writer_token, project_id, article["id"], "Point P", "12.40")
        resp = inv_client.patch(
            f"{_base(project_id)}/quotes/{quote['id']}",
            json={"unit_price_ht": "11.00"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["supplier_name"] == "Point P"
        assert body["unit_price_ht"] == 11.00
        assert body["tva_rate"] == 20.0


class TestPosteStores:
    """Where to go and buy — a poste keeps a list of shops, not just one."""

    def test_a_poste_holds_several_shops_in_order(self, inv_client, writer_token, reader_token, project_id, poste):
        for name, addr in [
            ("Leroy Merlin Ivry", "45 av de Verdun, 94200 Ivry-sur-Seine"),
            ("Point P Vitry", "12 rue Charles Fourier, 94400 Vitry"),
            ("Rexel Paris 13", None),
        ]:
            body = {"name": name}
            if addr:
                body["address"] = addr
            resp = inv_client.post(
                f"{_base(project_id)}/postes/{poste['id']}/stores",
                json=body,
                headers=_auth(writer_token),
            )
            assert resp.status_code == 201, resp.get_data(as_text=True)

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        # Module-scoped app fixture: sibling tests share the project, so find ours.
        target = next(p for p in tree["postes"] if p["id"] == poste["id"])
        assert [s["name"] for s in target["stores"]] == [
            "Leroy Merlin Ivry",
            "Point P Vitry",
            "Rexel Paris 13",
        ]
        assert target["stores"][0]["address"] == "45 av de Verdun, 94200 Ivry-sur-Seine"
        assert target["stores"][2]["address"] is None

    def test_poste_without_shops_returns_an_empty_list(self, inv_client, writer_token, reader_token, project_id):
        created = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Plomberie"}, headers=_auth(writer_token)
        ).get_json()
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        target = next(p for p in tree["postes"] if p["id"] == created["id"])
        assert target["stores"] == []

    def test_shop_can_be_renamed_without_losing_its_address(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Leroy Merlin", "address": "45 av de Verdun"},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"name": "Leroy Merlin Ivry"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["name"] == "Leroy Merlin Ivry"
        # The field-drop landmine: an omitted field must survive the PATCH.
        assert body["address"] == "45 av de Verdun"

    def test_address_can_be_cleared_explicitly(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Rexel", "address": "à confirmer"},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"address": None},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["address"] is None

    def test_deleting_a_shop_leaves_the_others(self, inv_client, writer_token, reader_token, project_id, poste):
        a = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Shop A"},
            headers=_auth(writer_token),
        ).get_json()
        inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Shop B"},
            headers=_auth(writer_token),
        )
        assert (
            inv_client.delete(f"{_base(project_id)}/stores/{a['id']}", headers=_auth(writer_token)).status_code == 204
        )
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        target = next(p for p in tree["postes"] if p["id"] == poste["id"])
        assert [s["name"] for s in target["stores"]] == ["Shop B"]

    def test_deleting_the_poste_removes_its_shops(self, inv_client, writer_token, reader_token, project_id):
        created = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Sol"}, headers=_auth(writer_token)
        ).get_json()
        store = inv_client.post(
            f"{_base(project_id)}/postes/{created['id']}/stores",
            json={"name": "Saint Maclou"},
            headers=_auth(writer_token),
        ).get_json()
        inv_client.delete(f"{_base(project_id)}/postes/{created['id']}", headers=_auth(writer_token))
        # The store went with it; addressing it directly must 404, not 500.
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"name": "x"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

    def test_blank_shop_name_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "   "},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_overlong_shop_name_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "x" * 161},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_blank_address_is_normalised_to_null(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Brico Dépôt", "address": "   "},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["address"] is None

    def test_read_only_member_cannot_add_a_shop(self, inv_client, reader_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Leroy Merlin"},
            headers=_auth(reader_token),
        )
        assert resp.status_code == 403

    def test_unknown_shop_is_404(self, inv_client, writer_token, project_id):
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{uuid.uuid4()}",
            json={"name": "x"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404


class TestValidationAndAuthorization:
    def test_quote_without_any_supplier_is_rejected(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"unit_price_ht": "9.99"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_negative_quantity_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Nope", "quantity": "-1"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_blank_poste_name_is_rejected(self, inv_client, writer_token, project_id):
        resp = inv_client.post(f"{_base(project_id)}/postes", json={"name": "   "}, headers=_auth(writer_token))
        assert resp.status_code == 400

    def test_read_only_member_cannot_create_a_poste(self, inv_client, reader_token, project_id):
        resp = inv_client.post(f"{_base(project_id)}/postes", json={"name": "Nope"}, headers=_auth(reader_token))
        assert resp.status_code == 403

    def test_read_only_member_can_read_the_tree(self, inv_client, reader_token, project_id):
        assert inv_client.get(_base(project_id), headers=_auth(reader_token)).status_code == 200

    def test_anonymous_request_is_rejected(self, inv_client, project_id):
        assert inv_client.get(_base(project_id)).status_code == 401

    def test_unknown_poste_is_404(self, inv_client, writer_token, project_id):
        resp = inv_client.patch(
            f"{_base(project_id)}/postes/{uuid.uuid4()}", json={"name": "x"}, headers=_auth(writer_token)
        )
        assert resp.status_code == 404


class TestCrossProjectIsolation:
    def test_store_of_another_project_is_not_reachable(
        self, inv_client, writer_token, chiffrage_world, project_id, poste
    ):
        """A valid store id under the wrong project must 404, never mutate."""
        store = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Leroy Merlin Ivry", "address": "45 av de Verdun"},
            headers=_auth(writer_token),
        ).get_json()
        other_project = chiffrage_world["other_project_id"]

        resp = inv_client.patch(
            f"/api/v1/projects/{other_project}/chiffrage/stores/{store['id']}",
            json={"name": "hijacked"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

        resp = inv_client.delete(
            f"/api/v1/projects/{other_project}/chiffrage/stores/{store['id']}",
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

        tree = inv_client.get(_base(project_id), headers=_auth(writer_token)).get_json()
        target = next(p for p in tree["postes"] if p["id"] == poste["id"])
        assert [s["name"] for s in target["stores"]] == ["Leroy Merlin Ivry"]

    def test_article_of_another_project_is_not_reachable(
        self, inv_client, writer_token, chiffrage_world, project_id, article
    ):
        """A valid id under the wrong project must 404, never mutate."""
        other_project = chiffrage_world["other_project_id"]
        resp = inv_client.patch(
            f"/api/v1/projects/{other_project}/chiffrage/articles/{article['id']}",
            json={"name": "hijacked"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

        tree = inv_client.get(_base(project_id), headers=_auth(writer_token)).get_json()
        assert _article_in_tree(tree, article["id"])["name"] == "Spot encastré"


class TestPosteLifecycle:
    def test_poste_can_be_renamed_and_its_note_cleared(self, inv_client, writer_token, project_id, poste):
        renamed = inv_client.patch(
            f"{_base(project_id)}/postes/{poste['id']}",
            json={"name": "Éclairage", "note": "RDC uniquement"},
            headers=_auth(writer_token),
        )
        assert renamed.status_code == 200
        assert renamed.get_json()["name"] == "Éclairage"
        assert renamed.get_json()["note"] == "RDC uniquement"

        cleared = inv_client.patch(
            f"{_base(project_id)}/postes/{poste['id']}",
            json={"note": None},
            headers=_auth(writer_token),
        )
        assert cleared.status_code == 200
        assert cleared.get_json()["note"] is None
        assert cleared.get_json()["name"] == "Éclairage", "clearing the note must not drop the name"

    def test_deleting_a_poste_removes_its_articles_and_quotes(
        self, inv_client, writer_token, reader_token, project_id, poste, article
    ):
        _add_quote(inv_client, writer_token, project_id, article["id"], "Leroy Merlin", "10.75")

        resp = inv_client.delete(f"{_base(project_id)}/postes/{poste['id']}", headers=_auth(writer_token))
        assert resp.status_code == 204

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        assert all(p["id"] != poste["id"] for p in tree["postes"])
        with pytest.raises(AssertionError):
            _article_in_tree(tree, article["id"])

    def test_postes_are_created_in_order_and_can_be_reordered(self, inv_client, writer_token, reader_token, project_id):
        made = []
        for name in ("Lot 1", "Lot 2", "Lot 3"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes", json={"name": name}, headers=_auth(writer_token)
                ).get_json()
            )
        first, second, third = made
        assert first["position"] < second["position"] < third["position"]

        resp = inv_client.post(
            f"{_base(project_id)}/postes/{third['id']}/reorder",
            json={"before_id": first["id"], "after_id": second["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert first["position"] < resp.get_json()["position"] < second["position"]

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        names = [p["name"] for p in tree["postes"] if p["name"].startswith("Lot ")]
        assert names == ["Lot 1", "Lot 3", "Lot 2"]

    def test_reordering_a_poste_of_another_project_is_404(
        self, inv_client, writer_token, chiffrage_world, project_id, poste
    ):
        resp = inv_client.post(
            f"/api/v1/projects/{chiffrage_world['other_project_id']}/chiffrage/postes/{poste['id']}/reorder",
            json={},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

    def test_over_long_poste_name_is_rejected(self, inv_client, writer_token, project_id):
        resp = inv_client.post(f"{_base(project_id)}/postes", json={"name": "x" * 121}, headers=_auth(writer_token))
        assert resp.status_code == 422


class TestQuoteValidationEdges:
    def test_vat_rate_above_100_is_rejected(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"supplier_name": "S", "unit_price_ht": "10", "tva_rate": "120"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_negative_price_is_rejected(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"supplier_name": "S", "unit_price_ht": "-1"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_clearing_the_supplier_name_of_a_free_text_quote_is_rejected(
        self, inv_client, writer_token, project_id, article
    ):
        """A quote must always identify its fournisseur somehow."""
        quote = _add_quote(inv_client, writer_token, project_id, article["id"], "Point P", "12.40")
        resp = inv_client.patch(
            f"{_base(project_id)}/quotes/{quote['id']}",
            json={"supplier_name": None},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_deleting_an_article_removes_its_quotes(self, inv_client, writer_token, reader_token, project_id, article):
        _add_quote(inv_client, writer_token, project_id, article["id"], "S", "10.00")
        resp = inv_client.delete(f"{_base(project_id)}/articles/{article['id']}", headers=_auth(writer_token))
        assert resp.status_code == 204
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        with pytest.raises(AssertionError):
            _article_in_tree(tree, article["id"])

    def test_unknown_quote_is_404(self, inv_client, writer_token, project_id):
        resp = inv_client.post(f"{_base(project_id)}/quotes/{uuid.uuid4()}/select", headers=_auth(writer_token))
        assert resp.status_code == 404

    def test_unknown_custom_unit_delete_is_404(self, inv_client, writer_token, project_id):
        resp = inv_client.delete(f"{_base(project_id)}/units/{uuid.uuid4()}", headers=_auth(writer_token))
        assert resp.status_code == 404


class TestPosteReorderEdges:
    """The three degenerate drop targets: head, tail, and empty payload."""

    @pytest.fixture
    def three_postes(self, inv_client, writer_token, project_id):
        made = []
        for name in ("P1", "P2", "P3"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes", json={"name": name}, headers=_auth(writer_token)
                ).get_json()
            )
        return made

    def test_only_after_id_moves_above_that_neighbour(self, inv_client, writer_token, project_id, three_postes):
        first, _, third = three_postes
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{third['id']}/reorder",
            json={"after_id": first["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["position"] < first["position"]

    def test_only_before_id_moves_below_that_neighbour(self, inv_client, writer_token, project_id, three_postes):
        first, _, third = three_postes
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{first['id']}/reorder",
            json={"before_id": third["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["position"] > third["position"]

    def test_no_neighbours_appends_to_the_end(self, inv_client, writer_token, project_id, three_postes):
        first, _, third = three_postes
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{first['id']}/reorder", json={}, headers=_auth(writer_token)
        )
        assert resp.status_code == 200
        assert resp.get_json()["position"] > third["position"]

    def test_article_reorder_with_no_neighbours_appends_within_its_poste(
        self, inv_client, writer_token, project_id, poste
    ):
        made = []
        for name in ("A", "B"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes/{poste['id']}/articles",
                    json={"name": name, "quantity": "1", "unit": "u"},
                    headers=_auth(writer_token),
                ).get_json()
            )
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{made[0]['id']}/reorder", json={}, headers=_auth(writer_token)
        )
        assert resp.status_code == 200
        assert resp.get_json()["position"] > made[1]["position"]
