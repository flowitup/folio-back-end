"""Unit tests for LinkPersonOnSignupUseCase — mocked repositories.

Covers: happy-path link + user_company_access creation, expiry (an
already-expired pending row must not be returned by the repository in the
first place — this test asserts the use case only acts on what the port
hands back), multi-match (several companies link in one call), no
enumeration (unconditional, no branch reveals whether a match existed), and
C1 (several independently-created duplicate Person rows for the same phone
merge into one survivor, with the losing rows repointed/cleaned up).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.company_persons.link_person_on_signup_usecase import LinkPersonOnSignupUseCase
from app.domain.entities.company_person import CompanyPerson
from app.domain.entities.person import Person


def _company_person(company_id, person_id, *, pending: bool = True) -> CompanyPerson:
    now = datetime.now(timezone.utc)
    return CompanyPerson(
        id=uuid4(),
        company_id=company_id,
        person_id=person_id,
        created_at=now,
        is_active=True,
        pending_expires_at=(now + timedelta(days=30)) if pending else None,
    )


def _person(person_id, *, user_id=None, created_at=None) -> Person:
    return Person(
        id=person_id,
        name="Some Person",
        normalized_name="some person",
        created_by_user_id=uuid4(),
        created_at=created_at or datetime.now(timezone.utc),
        user_id=user_id,
    )


def _usecase(person_repo=None, company_person_repo=None, access_repo=None) -> LinkPersonOnSignupUseCase:
    """Build the use case with sane no-op defaults for the ports not under test.

    Defaults are only applied to a port the caller did NOT already supply —
    a caller passing its own `person_repo`/`access_repo` mock has already
    configured every behavior it needs (e.g. `find_by_user_id`), and must not
    have that overwritten here.
    """
    if person_repo is None:
        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = None
    if company_person_repo is None:
        company_person_repo = MagicMock()
    if access_repo is None:
        access_repo = MagicMock()
        access_repo.find.return_value = None
        access_repo.list_for_user.return_value = []
    return LinkPersonOnSignupUseCase(
        person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
    )


class TestLinkPersonOnSignupHappyPath:
    def test_links_person_and_creates_company_access(self):
        user_id = uuid4()
        person_id = uuid4()
        company_id = uuid4()
        phone = "+33612345678"

        person_repo = MagicMock()
        person_repo.find_by_id.return_value = _person(person_id, user_id=None)
        person_repo.find_by_user_id.return_value = None

        company_person_repo = MagicMock()
        pending = _company_person(company_id, person_id)
        company_person_repo.list_pending_by_phone.return_value = [pending]

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, phone)

        assert linked == [company_id]
        person_repo.set_user_id.assert_called_once_with(person_id, user_id, commit=False)
        usecase._access.save.assert_called_once()
        saved_access = usecase._access.save.call_args.args[0]
        assert saved_access.user_id == user_id
        assert saved_access.company_id == company_id
        assert saved_access.role == "member"
        assert saved_access.is_primary is True  # first company for this user
        # Pending window cleared regardless of outcome.
        company_person_repo.save.assert_called_once()
        cleared = company_person_repo.save.call_args.args[0]
        assert cleared.pending_expires_at is None
        assert cleared.person_id == person_id  # sole match — no repoint needed

    def test_multiple_pending_profiles_link_every_company(self):
        user_id = uuid4()
        person_a, person_b = uuid4(), uuid4()
        company_a, company_b = uuid4(), uuid4()
        phone = "+33612345678"

        t0 = datetime.now(timezone.utc)
        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = None
        person_repo.find_by_id.side_effect = lambda pid: {
            person_a: _person(person_a, user_id=None, created_at=t0),
            person_b: _person(person_b, user_id=None, created_at=t0 + timedelta(seconds=1)),
        }[pid]

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [
            _company_person(company_a, person_a),
            _company_person(company_b, person_b),
        ]
        company_person_repo.list_for_person.return_value = []  # no other reference left

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, phone)

        assert set(linked) == {company_a, company_b}
        assert usecase._access.save.call_count == 2

    def test_no_pending_profiles_is_a_no_op(self):
        """Never raises when there is nothing to link — sign-up must never break on this."""
        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = []

        usecase = _usecase(company_person_repo=company_person_repo)

        linked = usecase.execute(uuid4(), "+33600000000")

        assert linked == []
        usecase._access.save.assert_not_called()
        usecase._persons.set_user_id.assert_not_called()


class TestLinkPersonOnSignupExpiry:
    def test_expired_pending_profile_is_never_seen_by_the_usecase(self):
        """The repository port contract (`list_pending_by_phone`) already filters
        expired rows — the use case must not need its own expiry check, and
        must not link anything when the port returns an empty list (the
        expired-row case)."""
        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = []  # repo already filtered the expired row out

        usecase = _usecase(company_person_repo=company_person_repo)

        linked = usecase.execute(uuid4(), "+33611112222")

        assert linked == []
        usecase._access.save.assert_not_called()


class TestLinkPersonOnSignupRecycledNumberSafety:
    def test_does_not_steal_a_person_already_linked_to_a_different_user(self):
        """Finding 5 (recycled numbers): a pending profile's Person already
        linked to a DIFFERENT account must not be silently re-linked."""
        user_id = uuid4()
        other_user_id = uuid4()
        person_id = uuid4()
        company_id = uuid4()

        person_repo = MagicMock()
        person_repo.find_by_id.return_value = _person(person_id, user_id=other_user_id)

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [_company_person(company_id, person_id)]

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, "+33612345678")

        assert linked == []
        usecase._persons.set_user_id.assert_not_called()
        usecase._access.save.assert_not_called()

    def test_already_linked_to_the_same_user_still_clears_pending_and_attaches(self):
        user_id = uuid4()
        person_id = uuid4()
        company_id = uuid4()

        person_repo = MagicMock()
        person_repo.find_by_id.return_value = _person(person_id, user_id=user_id)
        person_repo.find_by_user_id.return_value = _person(person_id, user_id=user_id)

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [_company_person(company_id, person_id)]

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, "+33612345678")

        assert linked == [company_id]
        usecase._persons.set_user_id.assert_not_called()  # already linked — no redundant write
        usecase._access.save.assert_called_once()


class TestLinkPersonOnSignupMultiCompanyMerge:
    """C1: several companies independently created a duplicate Person for the
    same phone before it ever signed up — exactly one survives."""

    def test_earliest_created_person_survives_others_repointed(self):
        user_id = uuid4()
        person_a, person_b, person_c = uuid4(), uuid4(), uuid4()
        company_a, company_b, company_c = uuid4(), uuid4(), uuid4()
        t0 = datetime.now(timezone.utc)

        persons_by_id = {
            person_a: _person(person_a, created_at=t0 + timedelta(seconds=5)),
            person_b: _person(person_b, created_at=t0),  # earliest — survivor
            person_c: _person(person_c, created_at=t0 + timedelta(seconds=10)),
        }
        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = None
        person_repo.find_by_id.side_effect = lambda pid: persons_by_id[pid]
        person_repo.has_references.return_value = False

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [
            _company_person(company_a, person_a),
            _company_person(company_b, person_b),
            _company_person(company_c, person_c),
        ]
        company_person_repo.list_for_person.return_value = []  # fully drained after repoint

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, "+33699999999")

        assert set(linked) == {company_a, company_b, company_c}
        # Survivor (person_b, earliest) gets user_id set exactly once.
        person_repo.set_user_id.assert_called_once_with(person_b, user_id, commit=False)
        # Every saved profile ends up pointing at the survivor.
        saved_profiles = [call.args[0] for call in company_person_repo.save.call_args_list]
        assert {p.person_id for p in saved_profiles} == {person_b}
        assert all(p.pending_expires_at is None for p in saved_profiles)
        # Both losing duplicates, with no remaining company_persons reference
        # and no Worker reference, get deleted.
        deleted_ids = {call.args[0] for call in person_repo.delete.call_args_list}
        assert deleted_ids == {person_a, person_c}

    def test_duplicate_still_referenced_by_worker_is_left_unlinked(self):
        user_id = uuid4()
        person_a, person_b = uuid4(), uuid4()
        company_a, company_b = uuid4(), uuid4()
        t0 = datetime.now(timezone.utc)

        persons_by_id = {
            person_a: _person(person_a, created_at=t0),  # survivor
            person_b: _person(person_b, created_at=t0 + timedelta(seconds=5)),
        }
        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = None
        person_repo.find_by_id.side_effect = lambda pid: persons_by_id[pid]
        person_repo.has_references.return_value = True  # a Worker still points at person_b

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [
            _company_person(company_a, person_a),
            _company_person(company_b, person_b),
        ]
        company_person_repo.list_for_person.return_value = []

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        usecase.execute(user_id, "+33688888888")

        person_repo.delete.assert_not_called()

    def test_duplicate_still_profiled_in_another_company_is_left_unlinked(self):
        """A duplicate reused across two OTHER companies by the same admin
        keeps one company_persons row after this batch — never deleted while
        still referenced somewhere."""
        user_id = uuid4()
        person_a, person_b = uuid4(), uuid4()
        company_a, company_b = uuid4(), uuid4()
        t0 = datetime.now(timezone.utc)

        persons_by_id = {
            person_a: _person(person_a, created_at=t0),  # survivor
            person_b: _person(person_b, created_at=t0 + timedelta(seconds=5)),
        }
        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = None
        person_repo.find_by_id.side_effect = lambda pid: persons_by_id[pid]
        person_repo.has_references.return_value = False

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [
            _company_person(company_a, person_a),
            _company_person(company_b, person_b),
        ]
        # person_b still has a row in some OTHER (non-pending) company.
        company_person_repo.list_for_person.return_value = [MagicMock()]

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        usecase.execute(user_id, "+33677777777")

        person_repo.delete.assert_not_called()

    def test_existing_user_person_is_always_the_survivor(self):
        """The signing-up user already has a Person (e.g. attached earlier via
        an accepted invitation) — that Person wins even over an
        earlier-created pending duplicate."""
        user_id = uuid4()
        existing_person_id = uuid4()
        pending_person_id = uuid4()
        company_id = uuid4()
        t0 = datetime.now(timezone.utc)

        existing_person = _person(existing_person_id, user_id=user_id, created_at=t0 + timedelta(days=1))
        pending_person = _person(pending_person_id, created_at=t0)  # earlier, but not the survivor

        person_repo = MagicMock()
        person_repo.find_by_user_id.return_value = existing_person
        person_repo.find_by_id.return_value = pending_person
        person_repo.has_references.return_value = False

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [_company_person(company_id, pending_person_id)]
        company_person_repo.list_for_person.return_value = []

        usecase = _usecase(person_repo=person_repo, company_person_repo=company_person_repo)

        linked = usecase.execute(user_id, "+33666666666")

        assert linked == [company_id]
        person_repo.set_user_id.assert_not_called()  # existing_person already has user_id
        saved_profile = company_person_repo.save.call_args.args[0]
        assert saved_profile.person_id == existing_person_id
        person_repo.delete.assert_called_once_with(pending_person_id)
