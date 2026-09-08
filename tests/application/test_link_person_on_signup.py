"""Unit tests for LinkPersonOnSignupUseCase — mocked repositories.

Covers: happy-path link + user_company_access creation, expiry (an
already-expired pending row must not be returned by the repository in the
first place — this test asserts the use case only acts on what the port
hands back), multi-match (several companies link in one call), no
enumeration (unconditional, no branch reveals whether a match existed).
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


def _person(person_id, *, user_id=None) -> Person:
    return Person(
        id=person_id,
        name="Some Person",
        normalized_name="some person",
        created_by_user_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
    )


class TestLinkPersonOnSignupHappyPath:
    def test_links_person_and_creates_company_access(self):
        user_id = uuid4()
        person_id = uuid4()
        company_id = uuid4()
        phone = "+33612345678"

        person_repo = MagicMock()
        person_repo.find_by_id.return_value = _person(person_id, user_id=None)

        company_person_repo = MagicMock()
        pending = _company_person(company_id, person_id)
        company_person_repo.list_pending_by_phone.return_value = [pending]

        access_repo = MagicMock()
        access_repo.find.return_value = None
        access_repo.list_for_user.return_value = []

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(user_id, phone)

        assert linked == [company_id]
        person_repo.set_user_id.assert_called_once_with(person_id, user_id)
        access_repo.save.assert_called_once()
        saved_access = access_repo.save.call_args.args[0]
        assert saved_access.user_id == user_id
        assert saved_access.company_id == company_id
        assert saved_access.role == "member"
        assert saved_access.is_primary is True  # first company for this user
        # Pending window cleared regardless of outcome.
        company_person_repo.save.assert_called_once()
        cleared = company_person_repo.save.call_args.args[0]
        assert cleared.pending_expires_at is None

    def test_multiple_pending_profiles_link_every_company(self):
        user_id = uuid4()
        person_a, person_b = uuid4(), uuid4()
        company_a, company_b = uuid4(), uuid4()
        phone = "+33612345678"

        person_repo = MagicMock()
        person_repo.find_by_id.side_effect = lambda pid: _person(pid, user_id=None)

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [
            _company_person(company_a, person_a),
            _company_person(company_b, person_b),
        ]

        access_repo = MagicMock()
        access_repo.find.return_value = None
        access_repo.list_for_user.return_value = []

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(user_id, phone)

        assert set(linked) == {company_a, company_b}
        assert access_repo.save.call_count == 2

    def test_no_pending_profiles_is_a_no_op(self):
        """Never raises when there is nothing to link — sign-up must never break on this."""
        person_repo = MagicMock()
        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = []
        access_repo = MagicMock()

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(uuid4(), "+33600000000")

        assert linked == []
        access_repo.save.assert_not_called()
        person_repo.set_user_id.assert_not_called()


class TestLinkPersonOnSignupExpiry:
    def test_expired_pending_profile_is_never_seen_by_the_usecase(self):
        """The repository port contract (`list_pending_by_phone`) already filters
        expired rows — the use case must not need its own expiry check, and
        must not link anything when the port returns an empty list (the
        expired-row case)."""
        access_repo = MagicMock()
        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = []  # repo already filtered the expired row out
        person_repo = MagicMock()

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(uuid4(), "+33611112222")

        assert linked == []
        access_repo.save.assert_not_called()


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

        access_repo = MagicMock()

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(user_id, "+33612345678")

        assert linked == []
        person_repo.set_user_id.assert_not_called()
        access_repo.save.assert_not_called()

    def test_already_linked_to_the_same_user_still_clears_pending_and_attaches(self):
        user_id = uuid4()
        person_id = uuid4()
        company_id = uuid4()

        person_repo = MagicMock()
        person_repo.find_by_id.return_value = _person(person_id, user_id=user_id)

        company_person_repo = MagicMock()
        company_person_repo.list_pending_by_phone.return_value = [_company_person(company_id, person_id)]

        access_repo = MagicMock()
        access_repo.find.return_value = None
        access_repo.list_for_user.return_value = []

        usecase = LinkPersonOnSignupUseCase(
            person_repo=person_repo, company_person_repo=company_person_repo, access_repo=access_repo
        )

        linked = usecase.execute(user_id, "+33612345678")

        assert linked == [company_id]
        person_repo.set_user_id.assert_not_called()  # already linked — no redundant write
        access_repo.save.assert_called_once()
