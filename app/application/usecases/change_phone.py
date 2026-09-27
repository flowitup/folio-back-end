"""A signed-in user moves their account to a new phone number, proven by an SMS code.

The phone is the sign-in identity (phone + SMS code is the only way in), so the new number must be
proven: ``RequestPhoneChangeCodeUseCase`` texts a code to the NEW number and
``ConfirmPhoneChangeUseCase`` swaps the number once that code checks out. The codes are stored with
``OtpPurpose.PHONE_CHANGE`` and bound to the requesting user, so they never sign anyone in, and a
sign-in code never changes a number. Same rules as sign-in: French numbers only, same TTL, resend
throttle, hourly cap and attempt limit (``_issue_code`` / ``_consume_code``). A number already used
by another account is refused without saying whose it is. The caller's tokens identify the user,
not the phone, so the session stays valid after the change.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable
from uuid import UUID

from app.application.ports.login_otp_repository import LoginOtpRepositoryPort
from app.application.ports.sms_sender import SmsSenderPort
from app.application.ports.user_repository import UserRepositoryPort
from app.application.usecases.otp_login import (
    RequestOtpResult,
    _consume_code,
    _issue_code,
    _reviewer_code_for,
    _utcnow,
)
from app.domain.entities.login_otp import OtpPurpose
from app.domain.entities.user import User
from app.domain.exceptions.auth_exceptions import (
    OtpInvalidError,
    PhoneAlreadyRegisteredError,
    PhoneUnchangedError,
    UserInactiveError,
)
from app.domain.value_objects.phone_number import normalize_french_phone

logger = logging.getLogger(__name__)

# ASCII on purpose, like the sign-in SMS: one GSM-7 segment, no Unicode surcharge.
PHONE_CHANGE_MESSAGE = "Folio: ma xac nhan so dien thoai moi cua ban la {code}. Ma het han sau {minutes} phut."


def _check_new_phone(users: UserRepositoryPort, user_id: UUID, raw_phone: str) -> tuple[User, str]:
    """Return the active caller and the normalised new number, or raise.

    Raises ``InvalidPhoneNumberError`` (not a French number), ``UserInactiveError``,
    ``PhoneUnchangedError`` (already the caller's number) or ``PhoneAlreadyRegisteredError``
    (another account signs in with it — never says which).
    """
    phone = normalize_french_phone(raw_phone)
    user = users.find_by_id(user_id)
    if user is None or not user.is_active:
        raise UserInactiveError("User account is deactivated")
    if phone == user.phone:
        raise PhoneUnchangedError("This is already your phone number")
    owner = users.find_by_phone(phone)
    # The store-review number signs in with a fixed code; nobody may move onto it.
    if (owner is not None and owner.id != user.id) or _reviewer_code_for(phone) is not None:
        raise PhoneAlreadyRegisteredError("This phone number is already used by another account")
    return user, phone


class RequestPhoneChangeCodeUseCase:
    """Text a phone-change code to the new number the caller wants to sign in with."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        sms: SmsSenderPort,
        *,
        ttl_seconds: int = 300,
        resend_after_seconds: int = 60,
        hourly_max: int = 5,
        message: str = PHONE_CHANGE_MESSAGE,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._users = user_repo
        self._otps = otp_repo
        self._sms = sms
        self._ttl = ttl_seconds
        self._resend_after = resend_after_seconds
        self._hourly_max = hourly_max
        self._message = message
        self._clock = clock

    def execute(self, user_id: UUID, raw_phone: str) -> RequestOtpResult:
        user, phone = _check_new_phone(self._users, user_id, raw_phone)
        _issue_code(
            self._otps,
            self._sms,
            phone=phone,
            user_id=user.id,
            now=self._clock(),
            ttl=self._ttl,
            resend_after=self._resend_after,
            hourly_max=self._hourly_max,
            message=self._message,
            purpose=OtpPurpose.PHONE_CHANGE,
        )
        return RequestOtpResult(expires_in=self._ttl)


class ConfirmPhoneChangeUseCase:
    """Swap the caller's sign-in number once the code texted to the new number checks out."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        *,
        max_attempts: int = 5,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._users = user_repo
        self._otps = otp_repo
        self._max_attempts = max_attempts
        self._clock = clock

    def execute(self, user_id: UUID, raw_phone: str, code: str) -> User:
        user, phone = _check_new_phone(self._users, user_id, raw_phone)
        otp = _consume_code(
            self._otps,
            phone=phone,
            code=code,
            now=self._clock(),
            max_attempts=self._max_attempts,
            purpose=OtpPurpose.PHONE_CHANGE,
        )
        if otp.user_id != user.id:
            # Another account's pending change to the same number: not this caller's code.
            raise OtpInvalidError("Invalid or expired code")
        user.phone = phone
        self._users.save(user)
        logger.info("auth.phone_change.confirmed user=%s", user.id)
        return user
