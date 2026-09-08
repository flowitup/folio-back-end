"""Attaching a user to a company always lists them in its directory.

The phone is a matching hint, not an identity: PostgreSQL keeps at most one
profile per `(company_id, phone_normalized)`, so a number an admin already used
for a pending profile must not make the attachment fail — the profile is
created without the phone instead.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.application.company_persons.ensure_company_person import ensure_company_person
from app.domain.entities.company_person import CompanyPerson
from app.domain.entities.person import Person

NOW = datetime.now(timezone.utc)


class FakePersons:
    def __init__(self, person=None):
        self.person = person
        self.created = []

    def find_by_user_id(self, user_id):
        return self.person

    def create(self, person, commit=False):
        self.created.append(person)
        self.person = person
        return person


class FakeCompanyPersons:
    def __init__(self, by_phone=None):
        self.rows = []
        self._by_phone = by_phone or {}

    def find(self, company_id, person_id):
        return next((r for r in self.rows if r.company_id == company_id and r.person_id == person_id), None)

    def find_by_phone(self, company_id, phone_normalized):
        return self._by_phone.get((company_id, phone_normalized))

    def save(self, profile):
        self.rows = [r for r in self.rows if r.id != profile.id] + [profile]
        return profile


def _person(phone="+33611111111", user_id=None):
    return Person(
        id=uuid4(),
        name="Attached User",
        normalized_name="attached user",
        created_by_user_id=user_id or uuid4(),
        created_at=NOW,
        phone=phone,
        phone_normalized=phone,
        user_id=user_id,
    )


def _call(persons, company_persons, user_id, company_id):
    ensure_company_person(
        persons=persons,
        company_persons=company_persons,
        users=SimpleNamespace(find_by_id=lambda uid: None),
        user_id=user_id,
        company_id=company_id,
        now=NOW,
    )


def test_creates_a_linked_profile_carrying_the_phone():
    user_id, company_id = uuid4(), uuid4()
    persons = FakePersons(_person(user_id=user_id))
    company_persons = FakeCompanyPersons()

    _call(persons, company_persons, user_id, company_id)

    assert len(company_persons.rows) == 1
    assert company_persons.rows[0].phone_normalized == "+33611111111"
    assert company_persons.rows[0].is_active is True


def test_phone_already_used_in_the_company_yields_a_profile_without_it():
    """The attachment is what the caller asked for; the phone is only a hint."""
    user_id, company_id = uuid4(), uuid4()
    phone = "+33622222222"
    conflicting = CompanyPerson(
        id=uuid4(),
        company_id=company_id,
        person_id=uuid4(),  # a pending profile an admin added by phone
        created_at=NOW,
        is_active=True,
        phone_normalized=phone,
    )
    persons = FakePersons(_person(phone=phone, user_id=user_id))
    company_persons = FakeCompanyPersons(by_phone={(company_id, phone): conflicting})

    _call(persons, company_persons, user_id, company_id)

    assert len(company_persons.rows) == 1
    assert company_persons.rows[0].phone_normalized is None
    assert company_persons.rows[0].person_id == persons.person.id


def test_a_booted_profile_is_reactivated_not_duplicated():
    user_id, company_id = uuid4(), uuid4()
    person = _person(user_id=user_id)
    persons = FakePersons(person)
    company_persons = FakeCompanyPersons()
    company_persons.rows.append(
        CompanyPerson(
            id=uuid4(),
            company_id=company_id,
            person_id=person.id,
            created_at=NOW,
            is_active=False,
            phone_normalized=person.phone_normalized,
        )
    )

    _call(persons, company_persons, user_id, company_id)

    assert len(company_persons.rows) == 1
    assert company_persons.rows[0].is_active is True


def test_without_directory_repositories_the_attachment_still_happens():
    """A caller wired without the directory simply skips the profile."""
    ensure_company_person(
        persons=None,
        company_persons=None,
        users=None,
        user_id=uuid4(),
        company_id=uuid4(),
        now=NOW,
    )


def test_an_active_profile_is_left_untouched():
    user_id, company_id = uuid4(), uuid4()
    person = _person(user_id=user_id)
    persons = FakePersons(person)
    company_persons = FakeCompanyPersons()
    existing = CompanyPerson(
        id=uuid4(),
        company_id=company_id,
        person_id=person.id,
        created_at=NOW,
        is_active=True,
        phone_normalized=person.phone_normalized,
    )
    company_persons.rows.append(existing)

    _call(persons, company_persons, user_id, company_id)

    assert company_persons.rows == [existing]
    assert dataclasses.asdict(company_persons.rows[0]) == dataclasses.asdict(existing)
