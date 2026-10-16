"""Mac #10: the code SMS is written in the app's language (Vietnamese stays the default)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.application.usecases.change_phone import PHONE_CHANGE_MESSAGE
from app.application.usecases.otp_login import (
    DEFAULT_MESSAGE,
    SIGNUP_MESSAGE,
    RequestOtpUseCase,
    RequestSignupOtpUseCase,
    localized_message,
)

PHONE = "+33611223344"


def _sent_text(use_case_cls, user, locale):
    users = MagicMock()
    users.find_by_phone.return_value = user
    otps = MagicMock()
    otps.latest_for_phone.return_value = None
    otps.count_created_since.return_value = 0
    sms = MagicMock()
    use_case_cls(users, otps, sms).execute(PHONE, locale=locale)
    return sms.send.call_args[0][1]


def test_sign_in_code_follows_the_locale():
    user = MagicMock(id=uuid4(), is_active=True)
    assert _sent_text(RequestOtpUseCase, user, "fr").startswith("Folio : votre code de connexion est ")
    assert _sent_text(RequestOtpUseCase, user, "en").startswith("Folio: your sign-in code is ")
    assert _sent_text(RequestOtpUseCase, user, None).startswith("Folio: ma dang nhap")


def test_sign_up_code_follows_the_locale():
    assert "création de compte" in _sent_text(RequestSignupOtpUseCase, None, "fr-FR")
    assert _sent_text(RequestSignupOtpUseCase, None, "xx").startswith("Folio: ma dang ky")


def test_a_configured_text_is_never_replaced():
    assert localized_message("Custom {code}", DEFAULT_MESSAGE, {"fr": "x"}, "fr") == "Custom {code}"
    assert localized_message(SIGNUP_MESSAGE, SIGNUP_MESSAGE, {"fr": "fr {code}"}, "FR") == "fr {code}"
    assert PHONE_CHANGE_MESSAGE.startswith("Folio:")
