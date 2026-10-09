"""How export files name the user who generated them (get_requester_label)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api._helpers import requester_identity
from app.api._helpers.requester_identity import get_requester_label
from app.domain.entities.user import User


class _Users:
    def __init__(self, user: User | None) -> None:
        self._user = user

    def find_by_id(self, user_id):  # noqa: ANN001
        return self._user


def _label(monkeypatch, user: User | None) -> str:
    monkeypatch.setattr(requester_identity, "get_jwt_identity", lambda: str(uuid4()))
    return get_requester_label(_Users(user))


@pytest.mark.parametrize(
    ("display_name", "email", "phone", "expected"),
    [
        ("Alice Martin", "alice@example.com", "+33612345678", "Alice Martin"),
        (None, "alice@example.com", "+33612345678", "alice@example.com"),
        # A phone sign-up's synthetic address is never printed: the phone is.
        (None, "phone-33612345678@no-email.folio.flowitup.com", "+33612345678", "+33612345678"),
        ("  ", "phone-33612345678@no-email.folio.flowitup.com", None, "unknown"),
    ],
)
def test_label_prefers_name_then_real_email_then_phone(monkeypatch, display_name, email, phone, expected) -> None:
    user = User(id=uuid4(), email=email, display_name=display_name, phone=phone)
    assert _label(monkeypatch, user) == expected


def test_unknown_user_or_identity(monkeypatch) -> None:
    assert _label(monkeypatch, None) == "unknown"
    monkeypatch.setattr(requester_identity, "get_jwt_identity", lambda: None)
    assert get_requester_label(_Users(None)) == "unknown"
