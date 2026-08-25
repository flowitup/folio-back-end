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
    """Where to go and buy — shops belong to the project and prices point at them.

    Names are unique per project, so each test here uses its own shop names:
    the app fixture is module-scoped and every test shares one project.
    """

    def test_a_project_holds_several_shops_in_order(self, inv_client, writer_token, reader_token, project_id):
        for name, addr in [
            ("Leroy Merlin Ivry", "45 av de Verdun, 94200 Ivry-sur-Seine"),
            ("Point P Vitry", "12 rue Charles Fourier, 94400 Vitry"),
            ("Rexel Paris 13", None),
        ]:
            body = {"name": name}
            if addr:
                body["address"] = addr
            resp = inv_client.post(
                f"{_base(project_id)}/stores",
                json=body,
                headers=_auth(writer_token),
            )
            assert resp.status_code == 201, resp.get_data(as_text=True)

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        # Module-scoped app fixture: sibling tests share the project, so filter.
        mine = ["Leroy Merlin Ivry", "Point P Vitry", "Rexel Paris 13"]
        assert [s["name"] for s in tree["stores"] if s["name"] in mine] == mine
        by_name = {s["name"]: s for s in tree["stores"]}
        assert by_name["Leroy Merlin Ivry"]["address"] == "45 av de Verdun, 94200 Ivry-sur-Seine"
        assert by_name["Rexel Paris 13"]["address"] is None

    def test_a_new_poste_carries_no_shop_of_its_own(self, inv_client, writer_token, reader_token, project_id):
        """Shops live on the project now; a poste never owns its own list."""
        created = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Plomberie"}, headers=_auth(writer_token)
        ).get_json()
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        target = next(p for p in tree["postes"] if p["id"] == created["id"])
        assert "stores" not in target
        assert isinstance(tree["stores"], list)

    def test_the_same_shop_name_cannot_be_entered_twice(self, inv_client, writer_token, project_id):
        """Two spellings of one shop would split its basket, so the second is refused."""
        first = inv_client.post(
            f"{_base(project_id)}/stores", json={"name": "Brico Dépôt Créteil"}, headers=_auth(writer_token)
        )
        assert first.status_code == 201
        again = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "  brico dépôt créteil "},
            headers=_auth(writer_token),
        )
        assert again.status_code == 409

    def test_the_deprecated_poste_scoped_create_still_works(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        """Backend and frontend deploy in parallel; the old path must not 404."""
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/stores",
            json={"name": "Weldom Legacy"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["project_id"] == project_id
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        assert "Weldom Legacy" in [s["name"] for s in tree["stores"]]

    def test_shop_can_be_renamed_without_losing_its_address(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Castorama Rename", "address": "45 av de Verdun"},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"name": "Castorama Ivry"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["name"] == "Castorama Ivry"
        # The field-drop landmine: an omitted field must survive the PATCH.
        assert body["address"] == "45 av de Verdun"

    def test_address_can_be_cleared_explicitly(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/stores",
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

    def test_deleting_a_shop_leaves_the_others(self, inv_client, writer_token, reader_token, project_id):
        a = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Shop A"},
            headers=_auth(writer_token),
        ).get_json()
        inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Shop B"},
            headers=_auth(writer_token),
        )
        assert (
            inv_client.delete(f"{_base(project_id)}/stores/{a['id']}", headers=_auth(writer_token)).status_code == 204
        )
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        names = [s["name"] for s in tree["stores"]]
        assert "Shop B" in names and "Shop A" not in names

    def test_deleting_a_poste_leaves_the_project_shops_alone(self, inv_client, writer_token, project_id):
        """Shops outlive the sections that shopped there — they are project-level."""
        created = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Sol"}, headers=_auth(writer_token)
        ).get_json()
        store = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Saint Maclou"},
            headers=_auth(writer_token),
        ).get_json()
        inv_client.delete(f"{_base(project_id)}/postes/{created['id']}", headers=_auth(writer_token))
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"address": "still here"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200

    def test_blank_shop_name_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "   "},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_overlong_shop_name_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "x" * 161},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_website_is_saved_and_returned_in_the_tree(self, inv_client, writer_token, reader_token, project_id, poste):
        created = inv_client.post(
            f"{_base(project_id)}/stores",
            json={
                "name": "Leroy Merlin Website Test",
                "address": "45 av de Verdun",
                "website_url": "https://www.leroymerlin.fr/magasin/ivry",
            },
            headers=_auth(writer_token),
        ).get_json()
        assert created["website_url"] == "https://www.leroymerlin.fr/magasin/ivry"

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        store = next(s for s in tree["stores"] if s["id"] == created["id"])
        assert store["website_url"] == "https://www.leroymerlin.fr/magasin/ivry"

    def test_website_is_optional(self, inv_client, writer_token, project_id):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Rexel Optional Website"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["website_url"] is None

    def test_patching_only_the_website_keeps_name_and_address(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Point P", "address": "12 rue Charles Fourier"},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"website_url": "https://www.pointp.fr"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["website_url"] == "https://www.pointp.fr"
        # The field-drop landmine: omitted fields must survive the PATCH.
        assert body["name"] == "Point P"
        assert body["address"] == "12 rue Charles Fourier"

    def test_website_can_be_cleared_explicitly(self, inv_client, writer_token, project_id, poste):
        store = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Brico", "website_url": "https://example.com"},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/stores/{store['id']}",
            json={"website_url": None},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["website_url"] is None

    def test_blank_website_is_normalised_to_null(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Castorama", "website_url": "   "},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["website_url"] is None

    def test_overlong_website_is_rejected(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Shop", "website_url": "https://x.fr/" + "a" * 500},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_blank_address_is_normalised_to_null(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
            json={"name": "Brico Dépôt", "address": "   "},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["address"] is None

    def test_read_only_member_cannot_add_a_shop(self, inv_client, reader_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/stores",
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


class _FakeImageStorage:
    """In-memory stand-in for the S3 adapter: same three methods, a dict inside."""

    def __init__(self) -> None:
        self.objects: dict = {}

    def put(self, key, fileobj, content_type):
        self.objects[key] = (fileobj.read(), content_type)

    def get_stream(self, key):
        import io

        raw, ct = self.objects[key]
        return io.BytesIO(raw), len(raw), ct

    @staticmethod
    def build_key(article_id):
        return f"chiffrage-articles/{article_id}/image"


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestArticleImage:
    """Photos for the things to buy."""

    @pytest.fixture(autouse=True)
    def fake_storage(self, invitation_app):
        """Swap the S3 adapter for an in-memory one, restoring it afterwards."""
        from wiring import get_container

        with invitation_app.app_context():
            c = get_container()
            original = c.chiffrage_image_storage
            fake = _FakeImageStorage()
            c.chiffrage_image_storage = fake
            c.upload_chiffrage_article_image_usecase._storage = fake
            c.set_chiffrage_article_image_from_url_usecase._storage = fake
            c.get_chiffrage_article_image_usecase._storage = fake
            yield fake
            c.chiffrage_image_storage = original
            c.upload_chiffrage_article_image_usecase._storage = original
            c.set_chiffrage_article_image_from_url_usecase._storage = original
            c.get_chiffrage_article_image_usecase._storage = original

    def _upload(self, inv_client, token, project_id, article_id, data=_PNG, ct="image/png"):
        import io

        return inv_client.post(
            f"{_base(project_id)}/articles/{article_id}/image",
            data={"image": (io.BytesIO(data), "photo.png", ct)},
            content_type="multipart/form-data",
            headers=_auth(token),
        )

    def test_uploaded_photo_is_stored_and_streamed_back(
        self, inv_client, writer_token, reader_token, project_id, article
    ):
        assert self._upload(inv_client, writer_token, project_id, article["id"]).status_code == 201

        resp = inv_client.get(f"{_base(project_id)}/articles/{article['id']}/image", headers=_auth(reader_token))
        assert resp.status_code == 200
        assert resp.data == _PNG
        # User-supplied bytes must never be sniffed into something renderable.
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_tree_points_at_the_article_own_image(self, inv_client, writer_token, reader_token, project_id, article):
        self._upload(inv_client, writer_token, project_id, article["id"])
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        found = _article_in_tree(tree, article["id"])
        assert found["image_ref"] == {"kind": "article", "id": article["id"]}

    def test_article_without_a_photo_has_no_image_ref(self, inv_client, reader_token, project_id, article):
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        assert _article_in_tree(tree, article["id"])["image_ref"] is None

    def test_missing_image_is_404_not_500(self, inv_client, reader_token, project_id, article):
        resp = inv_client.get(f"{_base(project_id)}/articles/{article['id']}/image", headers=_auth(reader_token))
        assert resp.status_code == 404

    def test_photo_can_be_detached(self, inv_client, writer_token, reader_token, project_id, article):
        self._upload(inv_client, writer_token, project_id, article["id"])
        assert (
            inv_client.delete(
                f"{_base(project_id)}/articles/{article['id']}/image", headers=_auth(writer_token)
            ).status_code
            == 204
        )
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        assert _article_in_tree(tree, article["id"])["image_ref"] is None

    def test_non_image_upload_is_rejected(self, inv_client, writer_token, project_id, article):
        resp = self._upload(inv_client, writer_token, project_id, article["id"], b"%PDF-1.4", "application/pdf")
        assert resp.status_code == 415

    def test_missing_multipart_field_is_422(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/image",
            data={},
            content_type="multipart/form-data",
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_read_only_member_cannot_upload(self, inv_client, reader_token, project_id, article):
        assert self._upload(inv_client, reader_token, project_id, article["id"]).status_code == 403

    def test_image_from_url_refuses_a_host_off_the_allowlist(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/image-from-url",
            json={"url": "https://evil.example.com/x.png"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_image_from_url_refuses_plain_http(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/image-from-url",
            json={"url": "http://media.adeo.com/x.png"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_image_from_url_refuses_a_link_local_address(self, inv_client, writer_token, project_id, article):
        # The classic SSRF target — must not be reachable through this endpoint.
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/image-from-url",
            json={"url": "https://169.254.169.254/latest/meta-data/"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 400

    def test_image_from_url_stores_an_allowlisted_image(
        self, inv_client, writer_token, reader_token, project_id, article, monkeypatch
    ):
        import httpx

        class _Resp:
            status_code = 200
            content = _PNG
            headers = {"content-type": "image/png"}

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Resp())
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/image-from-url",
            json={"url": "https://media.adeo.com/some/product.png"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

        got = inv_client.get(f"{_base(project_id)}/articles/{article['id']}/image", headers=_auth(reader_token))
        assert got.status_code == 200 and got.data == _PNG

    def test_cross_project_article_image_is_404(self, inv_client, writer_token, chiffrage_world, project_id, article):
        other = chiffrage_world["other_project_id"]
        resp = self._upload(inv_client, writer_token, other, article["id"])
        assert resp.status_code == 404


class TestRooms:
    """The chantier's pièces: declared once, reused by every poste."""

    def _room(self, inv_client, token, project_id, name):
        return inv_client.post(f"{_base(project_id)}/rooms", json={"name": name}, headers=_auth(token))

    def test_rooms_are_listed_in_declared_order(self, inv_client, writer_token, reader_token, project_id):
        for n in ("Salon", "Cuisine", "Chambre 1"):
            assert self._room(inv_client, writer_token, project_id, n).status_code == 201
        rooms = inv_client.get(f"{_base(project_id)}/rooms", headers=_auth(reader_token)).get_json()
        names = [r["name"] for r in rooms]
        assert names[:3] == ["Salon", "Cuisine", "Chambre 1"]

    def test_duplicate_room_name_is_rejected(self, inv_client, writer_token, project_id):
        self._room(inv_client, writer_token, project_id, "Garage")
        assert self._room(inv_client, writer_token, project_id, "Garage").status_code == 409

    def test_blank_room_name_is_rejected(self, inv_client, writer_token, project_id):
        # Rejected by the schema (strip + min_length), so 422 rather than 400.
        assert self._room(inv_client, writer_token, project_id, "   ").status_code == 422

    def test_room_is_shared_across_postes(self, inv_client, writer_token, reader_token, project_id, poste):
        room = self._room(inv_client, writer_token, project_id, "Salle de bain").get_json()
        other = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Peinture"}, headers=_auth(writer_token)
        ).get_json()
        # The same room id is attachable from two different postes.
        for target in (poste["id"], other["id"]):
            resp = inv_client.post(
                f"{_base(project_id)}/postes/{target}/articles",
                json={"name": "Item", "quantity": "1", "unit": "u", "room_id": room["id"]},
                headers=_auth(writer_token),
            )
            assert resp.status_code == 201, resp.get_data(as_text=True)
            assert resp.get_json()["room_id"] == room["id"]

    def test_article_can_be_created_without_a_room(self, inv_client, writer_token, project_id, poste):
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Divers", "quantity": "1", "unit": "u"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["room_id"] is None

    def test_article_can_be_moved_to_another_room(self, inv_client, writer_token, project_id, poste):
        a = self._room(inv_client, writer_token, project_id, "Entrée").get_json()
        b = self._room(inv_client, writer_token, project_id, "Couloir").get_json()
        art = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Spot", "quantity": "2", "unit": "u", "room_id": a["id"]},
            headers=_auth(writer_token),
        ).get_json()
        resp = inv_client.patch(
            f"{_base(project_id)}/articles/{art['id']}",
            json={"room_id": b["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["room_id"] == b["id"]
        # The field-drop landmine: the rest of the line survives the move.
        assert body["name"] == "Spot" and body["quantity"] == 2.0

    def test_renaming_a_room_keeps_its_articles(self, inv_client, writer_token, reader_token, project_id, poste):
        room = self._room(inv_client, writer_token, project_id, "Sejour").get_json()
        art = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Applique", "quantity": "1", "unit": "u", "room_id": room["id"]},
            headers=_auth(writer_token),
        ).get_json()
        assert (
            inv_client.patch(
                f"{_base(project_id)}/rooms/{room['id']}",
                json={"name": "Séjour"},
                headers=_auth(writer_token),
            ).status_code
            == 200
        )
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        # Articles hold the id, so a rename does not detach them.
        assert _article_in_tree(tree, art["id"])["room_id"] == room["id"]
        assert any(r["name"] == "Séjour" for r in tree["rooms"])

    def test_deleting_a_room_keeps_its_articles_as_unassigned(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        room = self._room(inv_client, writer_token, project_id, "Cellier").get_json()
        art = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Étagère", "quantity": "1", "unit": "u", "room_id": room["id"]},
            headers=_auth(writer_token),
        ).get_json()
        assert (
            inv_client.delete(f"{_base(project_id)}/rooms/{room['id']}", headers=_auth(writer_token)).status_code == 204
        )
        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        found = _article_in_tree(tree, art["id"])
        # The item survives the room it was planned for.
        assert found is not None and found["room_id"] is None

    def test_room_of_another_project_cannot_be_attached(
        self, inv_client, writer_token, chiffrage_world, project_id, poste
    ):
        other = chiffrage_world["other_project_id"]
        foreign = inv_client.post(
            f"/api/v1/projects/{other}/chiffrage/rooms",
            json={"name": "Salon"},
            headers=_auth(writer_token),
        )
        if foreign.status_code != 201:
            pytest.skip("writer has no access to the sibling project")
        resp = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "X", "quantity": "1", "unit": "u", "room_id": foreign.get_json()["id"]},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

    def test_read_only_member_cannot_declare_a_room(self, inv_client, reader_token, project_id):
        assert self._room(inv_client, reader_token, project_id, "Bureau").status_code == 403

    def test_per_room_subtotals_add_up_to_the_poste_subtotal(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        r1 = self._room(inv_client, writer_token, project_id, "Salon TV").get_json()
        r2 = self._room(inv_client, writer_token, project_id, "Cuisine B").get_json()
        for room, qty, price in ((r1, "2", "10.00"), (r2, "3", "20.00")):
            art = inv_client.post(
                f"{_base(project_id)}/postes/{poste['id']}/articles",
                json={"name": "Item", "quantity": qty, "unit": "u", "room_id": room["id"]},
                headers=_auth(writer_token),
            ).get_json()
            _add_quote(inv_client, writer_token, project_id, art["id"], "LM", price)

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        target = next(p for p in tree["postes"] if p["id"] == poste["id"])
        subs = {s["room_id"]: s for s in target["room_subtotals"]}
        assert subs[r1["id"]]["subtotal_ht"] == 20.0
        assert subs[r2["id"]]["subtotal_ht"] == 60.0
        # The invariant that matters: rooms partition the poste exactly.
        assert round(sum(s["subtotal_ht"] for s in target["room_subtotals"]), 2) == target["subtotal_ht"]


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
            f"{_base(project_id)}/stores",
            json={"name": "Isolation Test Shop", "address": "45 av de Verdun"},
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
        assert "hijacked" not in [s["name"] for s in tree["stores"]]
        assert store["name"] in [s["name"] for s in tree["stores"]]

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


class TestPricesByShop:
    """A price points at one of the project's shops, and shops get compared."""

    def _shop(self, inv_client, writer_token, project_id, name):
        return inv_client.post(
            f"{_base(project_id)}/stores", json={"name": name}, headers=_auth(writer_token)
        ).get_json()

    def test_a_price_records_the_shop_it_came_from(self, inv_client, writer_token, project_id, article):
        shop = self._shop(inv_client, writer_token, project_id, "Shop For Price Link")
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"store_id": shop["id"], "unit_price_ht": "10.00", "tva_rate": "20"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["store_id"] == shop["id"]

    def test_a_shop_from_another_project_is_refused(
        self, inv_client, writer_token, chiffrage_world, project_id, article
    ):
        """A foreign shop would silently pollute this project's comparison."""
        other = chiffrage_world["other_project_id"]
        foreign = inv_client.post(
            f"{_base(other)}/stores", json={"name": "Foreign Shop"}, headers=_auth(writer_token)
        ).get_json()
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"store_id": foreign["id"], "unit_price_ht": "10.00"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 404

    def test_a_price_still_needs_to_say_where_it_came_from(self, inv_client, writer_token, project_id, article):
        resp = inv_client.post(
            f"{_base(project_id)}/articles/{article['id']}/quotes",
            json={"unit_price_ht": "10.00"},
            headers=_auth(writer_token),
        )
        assert resp.status_code == 422

    def test_deleting_a_shop_keeps_the_prices_recorded_there(
        self, inv_client, writer_token, reader_token, project_id, poste
    ):
        """ON DELETE SET NULL: losing a shop must never lose the costing work."""
        shop = self._shop(inv_client, writer_token, project_id, "Shop To Delete")
        art = inv_client.post(
            f"{_base(project_id)}/postes/{poste['id']}/articles",
            json={"name": "Survivor", "quantity": "1"},
            headers=_auth(writer_token),
        ).get_json()
        inv_client.post(
            f"{_base(project_id)}/articles/{art['id']}/quotes",
            json={"store_id": shop["id"], "supplier_name": "Shop To Delete", "unit_price_ht": "10.00"},
            headers=_auth(writer_token),
        )
        assert (
            inv_client.delete(f"{_base(project_id)}/stores/{shop['id']}", headers=_auth(writer_token)).status_code
            == 204
        )

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        target = next(a for p in tree["postes"] for a in p["articles"] if a["id"] == art["id"])
        assert len(target["quotes"]) == 1
        assert target["quotes"][0]["store_id"] is None
        assert target["quotes"][0]["supplier_name"] == "Shop To Delete"

    def test_the_tree_compares_shops_and_flags_incomplete_coverage(
        self, inv_client, writer_token, reader_token, project_id
    ):
        cheap = self._shop(inv_client, writer_token, project_id, "Cheap Partial Shop")
        full = self._shop(inv_client, writer_token, project_id, "Complete Shop")
        section = inv_client.post(
            f"{_base(project_id)}/postes", json={"name": "Comparison Section"}, headers=_auth(writer_token)
        ).get_json()
        made = []
        for name in ("A", "B"):
            made.append(
                inv_client.post(
                    f"{_base(project_id)}/postes/{section['id']}/articles",
                    json={"name": name, "quantity": "1"},
                    headers=_auth(writer_token),
                ).get_json()
            )
        # Cheap prices only the first item; Complete prices both, dearer.
        inv_client.post(
            f"{_base(project_id)}/articles/{made[0]['id']}/quotes",
            json={"store_id": cheap["id"], "unit_price_ht": "1.00"},
            headers=_auth(writer_token),
        )
        for art in made:
            inv_client.post(
                f"{_base(project_id)}/articles/{art['id']}/quotes",
                json={"store_id": full["id"], "unit_price_ht": "50.00"},
                headers=_auth(writer_token),
            )

        tree = inv_client.get(_base(project_id), headers=_auth(reader_token)).get_json()
        section_tree = next(p for p in tree["postes"] if p["id"] == section["id"])
        baskets = {b["store_id"]: b for b in section_tree["store_baskets"]}

        assert baskets[cheap["id"]]["covers_all"] is False
        assert baskets[cheap["id"]]["priced_article_count"] == 1
        assert baskets[cheap["id"]]["missing_article_ids"] == [made[1]["id"]]
        assert baskets[full["id"]]["covers_all"] is True
        assert baskets[full["id"]]["basket_ht"] == 100.0
        # The cheaper but incomplete shop must not head the list.
        assert section_tree["store_baskets"][0]["store_id"] == full["id"]
