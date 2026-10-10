"""A person's phone must read as a phone number; free text such as "hello world" is refused."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.application.persons.create_person import CreatePersonRequest, CreatePersonUseCase, InvalidPersonDataError


@pytest.mark.parametrize("phone", ["hello world", "call me maybe!", "<b>abc</b>", "12345"])
def test_free_text_phone_is_refused(phone):
    repo = Mock()
    with pytest.raises(InvalidPersonDataError, match="Invalid phone number"):
        CreatePersonUseCase(repo).execute(CreatePersonRequest(name="Ana", created_by_user_id=uuid4(), phone=phone))
    repo.create.assert_not_called()


@pytest.mark.parametrize("phone", ["06 12 34 56 78", "+84 912 345 678", "", None])
def test_phone_numbers_and_blank_are_kept_as_typed(phone):
    repo = Mock()
    repo.create.side_effect = lambda person: person
    created = CreatePersonUseCase(repo).execute(
        CreatePersonRequest(name="Ana", created_by_user_id=uuid4(), phone=phone)
    )
    assert created.phone == (phone or None)
