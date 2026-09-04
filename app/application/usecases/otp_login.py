"""Sign in with a phone number and a 6-digit code sent by SMS.

``RequestOtpUseCase`` never reveals whether a phone belongs to an account: unknown or inactive
numbers are silently ignored. Codes are hashed at rest, expire after ``ttl_seconds``, allow
``max_attempts`` guesses and are throttled per phone (``resend_after_seconds``, ``hourly_max``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List
from uuid import uuid4

from app.application.ports.login_otp_repository import LoginOtpRepositoryPort
from app.application.ports.sms_sender import SmsSenderPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.application.ports.user_repository import UserRepositoryPort
from app.application.usecases.login import LoginResult
from app.domain.entities.login_otp import LoginOtp
from app.domain.exceptions.auth_exceptions import OtpInvalidError, OtpThrottledError, UserInactiveError
from app.domain.services.authorization import AuthorizationService
from app.domain.value_objects.phone_number import normalize_phone

logger = logging.getLogger(__name__)

# ASCII on purpose: one GSM-7 segment, no Unicode surcharge.
DEFAULT_MESSAGE = "Folio: ma dang nhap cua ban la {code}. Ma het han sau {minutes} phut."


def _hash_code(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RequestOtpResult:
    expires_in: int


class RequestOtpUseCase:
    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        sms: SmsSenderPort,
        *,
        ttl_seconds: int = 300,
        resend_after_seconds: int = 60,
        hourly_max: int = 5,
        message: str = DEFAULT_MESSAGE,
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

    def execute(self, raw_phone: str) -> RequestOtpResult:
        phone = normalize_phone(raw_phone)
        now = self._clock()
        user = self._users.find_by_phone(phone)
        if user is None or not user.is_active:
            logger.info("auth.otp.request.unknown_phone")
            return RequestOtpResult(expires_in=self._ttl)

        latest = self._otps.latest_for_phone(phone)
        if latest is not None and (now - latest.created_at) < timedelta(seconds=self._resend_after):
            raise OtpThrottledError("A code was sent recently; wait before asking again")
        if self._otps.count_created_since(phone, now - timedelta(hours=1)) >= self._hourly_max:
            raise OtpThrottledError("Too many codes requested; try again later")

        code = f"{secrets.randbelow(10**6):06d}"
        self._otps.void_active(phone, now)
        self._otps.save(
            LoginOtp(
                id=uuid4(),
                user_id=user.id,
                phone=phone,
                code_hash=_hash_code(phone, code),
                expires_at=now + timedelta(seconds=self._ttl),
                created_at=now,
            )
        )
        self._sms.send(phone, self._message.format(code=code, minutes=max(1, self._ttl // 60)))
        return RequestOtpResult(expires_in=self._ttl)


class VerifyOtpUseCase:
    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        authorization_service: AuthorizationService,
        token_issuer: TokenIssuerPort,
        *,
        max_attempts: int = 5,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._users = user_repo
        self._otps = otp_repo
        self._authz = authorization_service
        self._tokens = token_issuer
        self._max_attempts = max_attempts
        self._clock = clock

    def execute(self, raw_phone: str, code: str) -> LoginResult:
        phone = normalize_phone(raw_phone)
        now = self._clock()
        otp = self._otps.latest_for_phone(phone)
        if otp is None or not otp.is_active(now) or otp.attempts >= self._max_attempts:
            raise OtpInvalidError("Invalid or expired code")
        if not hmac.compare_digest(otp.code_hash, _hash_code(phone, code.strip())):
            otp.attempts += 1
            self._otps.save(otp)
            raise OtpInvalidError("Invalid or expired code")
        otp.consumed_at = now
        self._otps.save(otp)

        user = self._users.find_by_id(otp.user_id)
        if user is None or not user.is_active:
            raise UserInactiveError("User account is deactivated")
        permissions: List[str] = list(self._authz.get_user_permissions(user.id))
        return LoginResult(
            user_id=user.id,
            access_token=self._tokens.create_access_token(user.id, {"permissions": permissions}),
            refresh_token=self._tokens.create_refresh_token(user.id),
            permissions=permissions,
        )
