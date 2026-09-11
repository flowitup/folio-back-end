"""API-level regression tests for the code-review fixes on
`feat/company-persons-onboarding` (C1, H1, H2, H4, H5, M1, M5, M6, M9, M10).

Uses a fresh `create_app()`-based module-scoped app (no second
`configure_container()` call) so the full DI wiring in `app/__init__.py`
applies — including the hoisted `LinkPersonOnSignupUseCase` wiring (M7) and
`DeleteCompanyUseCase`'s `authz_reader` (M5).

Fixture caveat: this app keeps ONE app context and ONE SQLAlchemy session
across every request the test client makes within a test — a use case that
only `flush()`es instead of `commit()`ing is invisible to a same-session
follow-up read. Every write-path test below calls `db.session.rollback()`
between the write and the read that verifies it, exactly like
`tests/api/test_member_grants.py::TestGrantWritesAreCommitted`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def cp_app():
    from app import create_app, db
    from config import TestingConfig

    class CpTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CpTestConfig)
    with test_app.app_context():
        db.create_all()

        # Record every "sent" SMS on the container's actual sms_sender
        # instance (RequestSignupOtpUseCase/VerifySignupOtpUseCase already
        # captured a reference to this object at construction time).
        from wiring import get_container

        sent: list[tuple[str, str]] = []
        container = get_container()
        container.sms_sender.send = lambda to, text: sent.append((to, text))
        test_app._sms_sent = sent

        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def cp_client(cp_app):
    return cp_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


def _make_user(app, email: str) -> str:
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_platform_admin(app, email: str) -> str:
    """A platform-ops account — needed for routes gated by `@require_admin`
    (platform-only), e.g. DELETE /companies/<id>, which M5's per-project-count
    check sits behind."""
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True, is_platform_ops=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_company(app, admin_id, *, name: str, join_code: str | None = None) -> str:
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(),
            legal_name=name,
            address="1 rue",
            created_by=admin_id,
            created_at=now,
            updated_at=now,
            join_code=join_code,
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        db.session.commit()
        return company.id


def _attach(app, user_id, company_id, role: str = "member", is_primary: bool = True) -> None:
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id, company_id=company_id, role=role, is_primary=is_primary, attached_at=now
            )
        )
        db.session.commit()


def _code_from_sms(app, phone: str) -> str:
    for to, text in reversed(app._sms_sent):
        if to == phone or to.endswith(phone[-9:]):
            match = re.search(r"\b(\d{6})\b", text)
            assert match, text
            return match.group(1)
    raise AssertionError(f"no SMS recorded to {phone}: {app._sms_sent}")


def _signup(client, app, phone: str, name: str) -> dict:
    req = client.post("/api/v1/auth/signup/request", json={"phone": phone})
    assert req.status_code == 202, req.get_data(as_text=True)
    resp = client.post(
        "/api/v1/auth/signup/verify", json={"phone": phone, "code": _code_from_sms(app, phone), "display_name": name}
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _add_member_by_phone(client, admin_token, company_id, phone, *, name=None, person_id=None):
    body = {"phone": phone}
    if name is not None:
        body["name"] = name
    if person_id is not None:
        body["person_id"] = person_id
    return client.post(f"/api/v1/companies/{company_id}/members", json=body, headers=_auth(admin_token))


# ---------------------------------------------------------------------------
# C1 — multi-company pending profiles merge into one Person on sign-up
# ---------------------------------------------------------------------------


class TestC1MultiCompanyPendingSignup:
    def test_two_companies_same_phone_merge_on_signup(self, cp_client, cp_app):
        admin_a_id = _make_user(cp_app, "c1_admin_a@test.com")
        admin_b_id = _make_user(cp_app, "c1_admin_b@test.com")
        company_a = _make_company(cp_app, admin_a_id, name="C1 Co A")
        company_b = _make_company(cp_app, admin_b_id, name="C1 Co B")
        token_a = _login(cp_client, "c1_admin_a@test.com")
        token_b = _login(cp_client, "c1_admin_b@test.com")

        phone = "0611110001"
        e164 = "+33611110001"

        resp_a = _add_member_by_phone(cp_client, token_a, company_a, phone, name="Worker A-side")
        assert resp_a.status_code == 201, resp_a.get_data(as_text=True)
        resp_b = _add_member_by_phone(cp_client, token_b, company_b, phone, name="Worker B-side")
        assert resp_b.status_code == 201, resp_b.get_data(as_text=True)
        person_a_id = resp_a.get_json()["person_id"]
        person_b_id = resp_b.get_json()["person_id"]
        assert person_a_id != person_b_id  # two independent admins → two distinct Person rows

        signed_up = _signup(cp_client, cp_app, phone, "Real Name")
        new_user_id = signed_up["user"]["id"]

        from app import db

        db.session.rollback()  # prove every write in the merge committed together

        with cp_app.app_context():
            persons_for_phone = db.session.query(PersonModel).filter_by(phone_normalized=e164).all()
            linked = [p for p in persons_for_phone if str(p.user_id) == new_user_id]
            assert len(linked) == 1, "exactly one Person must survive with user_id set"
            survivor_id = str(linked[0].id)
            assert survivor_id in (person_a_id, person_b_id)

            cp_rows = (
                db.session.query(CompanyPersonModel)
                .filter(CompanyPersonModel.company_id.in_([company_a, company_b]))
                .all()
            )
            assert len(cp_rows) == 2
            assert {str(r.person_id) for r in cp_rows} == {survivor_id}
            assert all(r.pending_expires_at is None for r in cp_rows)
            assert all(r.is_active for r in cp_rows)

            access_rows = (
                db.session.query(UserCompanyAccessModel)
                .filter(UserCompanyAccessModel.user_id == UUID(new_user_id))
                .all()
            )
            assert {str(r.company_id) for r in access_rows} == {str(company_a), str(company_b)}
            assert all(r.role == "member" for r in access_rows)

            # The losing duplicate Person (never referenced by a Worker) is
            # cleaned up rather than left as permanent dead weight.
            loser_id = person_b_id if survivor_id == person_a_id else person_a_id
            assert db.session.get(PersonModel, UUID(loser_id)) is None


# ---------------------------------------------------------------------------
# H1 — person_id resend must be validated against the real candidate set
# ---------------------------------------------------------------------------


class TestH1InvalidCandidateResend:
    def test_foreign_person_id_rejected_400_no_row_created(self, cp_client, cp_app):
        admin_a_id = _make_user(cp_app, "h1_admin_a@test.com")
        admin_b_id = _make_user(cp_app, "h1_admin_b@test.com")
        company_a = _make_company(cp_app, admin_a_id, name="H1 Co A")
        company_b = _make_company(cp_app, admin_b_id, name="H1 Co B")
        token_a = _login(cp_client, "h1_admin_a@test.com")
        token_b = _login(cp_client, "h1_admin_b@test.com")

        # A pending person exists in company A, created by its own admin.
        resp_a = _add_member_by_phone(cp_client, token_a, company_a, "0611110002", name="Foreign Candidate")
        assert resp_a.status_code == 201
        foreign_person_id = resp_a.get_json()["person_id"]

        # admin_b (of a DIFFERENT company, no relation to A) resends a fresh
        # add-by-phone with an unrelated phone but supplies A's person_id —
        # must be rejected: it is not a candidate for (this phone, admin_b's
        # admin companies).
        resp = _add_member_by_phone(cp_client, token_b, company_b, "0611110003", person_id=foreign_person_id)
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "name" not in body

        from app import db

        db.session.rollback()
        with cp_app.app_context():
            row = db.session.query(CompanyPersonModel).filter_by(company_id=company_b).all()
            assert row == []

    def test_random_uuid_person_id_rejected_400(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "h1_admin_c@test.com")
        company_id = _make_company(cp_app, admin_id, name="H1 Co C")
        token = _login(cp_client, "h1_admin_c@test.com")

        resp = _add_member_by_phone(cp_client, token, company_id, "0611110004", person_id=str(uuid4()))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# H2 — per-company phone uniqueness + M1 reactivation of a booted member
# ---------------------------------------------------------------------------


class TestH2PhoneConflictAndM1Reactivation:
    def test_different_person_same_phone_in_company_is_409(self, cp_client, cp_app):
        admin_a_id = _make_user(cp_app, "h2_admin_a@test.com")
        admin_b_id = _make_user(cp_app, "h2_admin_b@test.com")
        company_a = _make_company(cp_app, admin_a_id, name="H2 Co A")
        company_b = _make_company(cp_app, admin_b_id, name="H2 Co B")
        token_a = _login(cp_client, "h2_admin_a@test.com")
        token_b = _login(cp_client, "h2_admin_b@test.com")

        phone = "0611110005"

        resp_a = _add_member_by_phone(cp_client, token_a, company_a, phone, name="Person One")
        assert resp_a.status_code == 201
        person_one_id = resp_a.get_json()["person_id"]

        # admin_b (unrelated to A at this point) independently creates a
        # SECOND, distinct Person for the same phone in company B.
        resp_b = _add_member_by_phone(cp_client, token_b, company_b, phone, name="Person Two")
        assert resp_b.status_code == 201
        person_two_id = resp_b.get_json()["person_id"]
        assert person_two_id != person_one_id

        # admin_b is promoted to admin of company A AFTER both duplicates
        # exist — only now do their admin_company_ids overlap.
        _attach(cp_app, admin_b_id, company_a, role="admin", is_primary=False)

        # admin_b (now admin of both A and B) retries the SAME phone for
        # company A with no person_id — two candidates exist (person_one via
        # A, person_two via B) → 409 disambiguation.
        multi = _add_member_by_phone(cp_client, token_b, company_a, phone)
        assert multi.status_code == 409, multi.get_data(as_text=True)
        candidate_ids = {c["person_id"] for c in multi.get_json()["candidates"]}
        assert candidate_ids == {person_one_id, person_two_id}

        # Resending with person_two (a VALID candidate — passes H1) still
        # collides: company A already has person_one occupying this phone.
        resend = _add_member_by_phone(cp_client, token_b, company_a, phone, person_id=person_two_id)
        assert resend.status_code == 409, resend.get_data(as_text=True)
        assert resend.get_json()["person_id"] == person_one_id

        from app import db

        db.session.rollback()
        with cp_app.app_context():
            row = (
                db.session.query(CompanyPersonModel)
                .filter_by(company_id=company_a, person_id=UUID(person_two_id))
                .first()
            )
            assert row is None, "the conflicting insert must never have been committed"

    def test_rebooting_and_readding_reactivates_profile(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "m1_admin@test.com")
        company_id = _make_company(cp_app, admin_id, name="M1 Co")
        token = _login(cp_client, "m1_admin@test.com")
        phone = "0611110006"

        created = _add_member_by_phone(cp_client, token, company_id, phone, name="Reboot Target")
        assert created.status_code == 201
        person_id = created.get_json()["person_id"]

        from app import db

        with cp_app.app_context():
            db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=UUID(person_id)).update(
                {"is_active": False}
            )
            db.session.commit()

        readded = _add_member_by_phone(cp_client, token, company_id, phone, name="Reboot Target")
        assert readded.status_code == 201, readded.get_data(as_text=True)
        assert readded.get_json()["person_id"] == person_id

        db.session.rollback()
        with cp_app.app_context():
            row = (
                db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=UUID(person_id)).first()
            )
            assert row is not None and row.is_active is True


# ---------------------------------------------------------------------------
# H4 — join code rotates only on boot, only if one existed; company admins
# (not just platform superadmins) may read it back.
# ---------------------------------------------------------------------------


class TestH4JoinCodeSemantics:
    def test_self_detach_never_rotates_join_code(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "h4_admin_a@test.com")
        target_id = _make_user(cp_app, "h4_target_a@test.com")
        company_id = _make_company(cp_app, admin_id, name="H4 Co A", join_code="STABLE01")
        _attach(cp_app, target_id, company_id, role="member")
        token = _login(cp_client, "h4_target_a@test.com")

        resp = cp_client.delete(f"/api/v1/companies/{company_id}/access", headers=_auth(token))
        assert resp.status_code == 204

        from app import db

        db.session.rollback()
        with cp_app.app_context():
            company = db.session.get(CompanyModel, company_id)
            assert company.join_code == "STABLE01"

    def test_boot_does_not_mint_a_code_when_none_existed(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "h4_admin_b@test.com")
        target_id = _make_user(cp_app, "h4_target_b@test.com")
        company_id = _make_company(cp_app, admin_id, name="H4 Co B", join_code=None)
        _attach(cp_app, target_id, company_id, role="member")
        token = _login(cp_client, "h4_admin_b@test.com")

        resp = cp_client.delete(f"/api/v1/companies/{company_id}/access/{target_id}", headers=_auth(token))
        assert resp.status_code == 204

        from app import db

        db.session.rollback()
        with cp_app.app_context():
            company = db.session.get(CompanyModel, company_id)
            assert company.join_code is None

    def test_company_admin_reads_join_code_member_does_not(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "h4_admin_c@test.com")
        member_id = _make_user(cp_app, "h4_member_c@test.com")
        company_id = _make_company(cp_app, admin_id, name="H4 Co C", join_code="ADMINVIS")
        _attach(cp_app, member_id, company_id, role="member")
        admin_token = _login(cp_client, "h4_admin_c@test.com")
        member_token = _login(cp_client, "h4_member_c@test.com")

        as_admin = cp_client.get(f"/api/v1/companies/{company_id}", headers=_auth(admin_token))
        assert as_admin.status_code == 200
        assert as_admin.get_json()["join_code"] == "ADMINVIS"

        as_member = cp_client.get(f"/api/v1/companies/{company_id}", headers=_auth(member_token))
        assert as_member.status_code == 200
        assert "join_code" not in as_member.get_json()


# ---------------------------------------------------------------------------
# H5 — company directory / new-members feed do not N+1
# ---------------------------------------------------------------------------


class TestH5DirectoryQueryCount:
    def test_directory_query_count_does_not_grow_with_member_count(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "h5_admin@test.com")
        company_id = _make_company(cp_app, admin_id, name="H5 Co")
        token = _login(cp_client, "h5_admin@test.com")

        for i in range(8):
            resp = _add_member_by_phone(cp_client, token, company_id, f"061111{1000 + i}", name=f"H5 Worker {i}")
            assert resp.status_code == 201, resp.get_data(as_text=True)

        from app import db

        db.session.rollback()

        expected_counts = [8, 16]
        counts: list[int] = []
        for expected in expected_counts:
            queries = {"n": 0}

            def _before_cursor_execute(*args, **kwargs):
                queries["n"] += 1

            from sqlalchemy import event

            event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
            try:
                resp = cp_client.get(f"/api/v1/companies/{company_id}/persons", headers=_auth(token))
                assert resp.status_code == 200
                assert len(resp.get_json()["items"]) == expected
            finally:
                event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)
            counts.append(queries["n"])
            if expected == expected_counts[0]:
                # Double the membership count before the second measurement.
                for i in range(8, 16):
                    extra = _add_member_by_phone(
                        cp_client, token, company_id, f"061111{1000 + i}", name=f"H5 Worker {i}"
                    )
                    assert extra.status_code == 201
                db.session.rollback()

        # 8 members vs 16 members must not double the query count (a per-row
        # N+1 would); allow a small constant-factor cushion for unrelated
        # per-request bookkeeping queries (auth/session lookups).
        assert counts[1] <= counts[0] + 3, counts


# ---------------------------------------------------------------------------
# M5 — deleting a company that still owns projects is a 409, not an
# IntegrityError leak.
# ---------------------------------------------------------------------------


class TestM5DeleteCompanyWithProjects:
    def test_delete_company_with_projects_returns_409_with_count(self, cp_client, cp_app):
        owner_id = _make_user(cp_app, "m5_owner@test.com")
        _make_platform_admin(cp_app, "m5_platform_admin@test.com")
        company_id = _make_company(cp_app, owner_id, name="M5 Co")

        from app import db

        with cp_app.app_context():
            db.session.add(ProjectModel(id=uuid4(), name="M5 Project", owner_id=owner_id, company_id=company_id))
            db.session.commit()

        token = _login(cp_client, "m5_platform_admin@test.com")
        resp = cp_client.delete(f"/api/v1/companies/{company_id}", headers=_auth(token))
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert resp.get_json()["project_count"] == 1

        db.session.rollback()
        with cp_app.app_context():
            assert db.session.get(CompanyModel, company_id) is not None


# ---------------------------------------------------------------------------
# M6 — add-member response shape drops `pending`; the directory keeps it.
# ---------------------------------------------------------------------------


class TestM6ResponseShapeParity:
    def test_add_member_response_never_exposes_pending(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "m6_admin@test.com")
        company_id = _make_company(cp_app, admin_id, name="M6 Co")
        token = _login(cp_client, "m6_admin@test.com")

        resp = _add_member_by_phone(cp_client, token, company_id, "0611110099", name="M6 Worker")
        assert resp.status_code == 201
        body = resp.get_json()
        assert set(body.keys()) == {"person_id", "name", "phone"}

        from app import db

        db.session.rollback()
        directory = cp_client.get(f"/api/v1/companies/{company_id}/persons", headers=_auth(token))
        assert directory.status_code == 200
        assert "pending" in directory.get_json()["items"][0]


# ---------------------------------------------------------------------------
# M9 — project-membership removal normalizes UUID text on SQLite.
# ---------------------------------------------------------------------------


class TestM9ProjectMembershipRemoveNormalizesUuid:
    def test_remove_deletes_a_dashless_hex_row(self, cp_app):
        from app import db
        from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
            SqlAlchemyProjectMembershipRepository,
        )
        from sqlalchemy import text

        with cp_app.app_context():
            user_id = uuid4()
            project_id = uuid4()
            # Written in the OPPOSITE format from what `str(uuid4())` (dashed)
            # produces — mirrors the ORM UUID TypeDecorator's dashless-hex
            # SQLite storage that other insert paths in this codebase use.
            db.session.execute(
                text("INSERT INTO user_projects (user_id, project_id, assigned_at) " "VALUES (:uid, :pid, :at)"),
                {"uid": user_id.hex, "pid": project_id.hex, "at": datetime.now(timezone.utc)},
            )
            db.session.commit()

            repo = SqlAlchemyProjectMembershipRepository(db.session)
            removed = repo.remove(user_id, project_id)
            assert removed is True

            # Only assert OUR row is gone (module-scoped DB may have other rows).
            still_there = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE " "REPLACE(LOWER(CAST(user_id AS TEXT)), '-', '') = :uid"),
                {"uid": user_id.hex},
            ).fetchone()
            assert still_there is None


# ---------------------------------------------------------------------------
# M10 — /labor/roles write routes require company admin or manager.
# ---------------------------------------------------------------------------


class TestM10LaborRoleAuthorization:
    def test_member_cannot_create_role_manager_can(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "m10_admin@test.com")
        manager_id = _make_user(cp_app, "m10_manager@test.com")
        member_id = _make_user(cp_app, "m10_member@test.com")
        company_id = _make_company(cp_app, admin_id, name="M10 Co")
        _attach(cp_app, manager_id, company_id, role="manager")
        _attach(cp_app, member_id, company_id, role="member")
        manager_token = _login(cp_client, "m10_manager@test.com")
        member_token = _login(cp_client, "m10_member@test.com")

        forbidden = cp_client.post(
            "/api/v1/labor/roles",
            json={"name": "M10 Member Role", "color": "#E11D48"},
            query_string={"company_id": company_id},
            headers=_auth(member_token),
        )
        assert forbidden.status_code == 403

        allowed = cp_client.post(
            "/api/v1/labor/roles",
            json={"name": "M10 Manager Role", "color": "#E11D48"},
            query_string={"company_id": company_id},
            headers=_auth(manager_token),
        )
        assert allowed.status_code == 201, allowed.get_data(as_text=True)
        role_id = allowed.get_json()["id"]

        # Member cannot PATCH or DELETE it either.
        patch_forbidden = cp_client.patch(
            f"/api/v1/labor/roles/{role_id}", json={"name": "Renamed"}, headers=_auth(member_token)
        )
        assert patch_forbidden.status_code == 403
        delete_forbidden = cp_client.delete(f"/api/v1/labor/roles/{role_id}", headers=_auth(member_token))
        assert delete_forbidden.status_code == 403

        # Manager CAN update/delete their own company's role.
        patch_ok = cp_client.patch(
            f"/api/v1/labor/roles/{role_id}", json={"name": "Renamed By Manager"}, headers=_auth(manager_token)
        )
        assert patch_ok.status_code == 200

    def test_member_of_another_company_cannot_touch_this_companys_role(self, cp_client, cp_app):
        admin_id = _make_user(cp_app, "m10_admin_b@test.com")
        outsider_admin_id = _make_user(cp_app, "m10_outsider_admin@test.com")
        company_id = _make_company(cp_app, admin_id, name="M10 Co B")
        _make_company(cp_app, outsider_admin_id, name="M10 Co Outsider")
        admin_token = _login(cp_client, "m10_admin_b@test.com")
        outsider_token = _login(cp_client, "m10_outsider_admin@test.com")

        created = cp_client.post(
            "/api/v1/labor/roles",
            json={"name": "M10 Co B Role", "color": "#10B981"},
            query_string={"company_id": company_id},
            headers=_auth(admin_token),
        )
        assert created.status_code == 201
        role_id = created.get_json()["id"]

        cross_company = cp_client.delete(f"/api/v1/labor/roles/{role_id}", headers=_auth(outsider_token))
        assert cross_company.status_code == 403
