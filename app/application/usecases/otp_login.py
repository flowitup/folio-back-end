"""Sign in with a phone number and a 6-digit code sent by SMS.

Offered when the deployment's LOGIN_MODE is "phone" or "both". The refresh token lifetime
follows REFRESH_TOKEN_POLICY like password login (``persistent`` argument).

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
from typing import Callable, List, Optional
from uuid import UUID, uuid4

from app.application.ports.login_otp_repository import LoginOtpRepositoryPort
from app.application.ports.sms_sender import SmsSenderPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.application.ports.user_repository import UserRepositoryPort
from app.application.usecases.login import LoginResult
from app.domain.entities.login_otp import LoginOtp
from app.application.invitations.ports import RoleRepositoryPort
from app.application.ports.password_hasher import PasswordHasherPort
from app.domain.entities.user import User
from app.domain.exceptions.auth_exceptions import (
    OtpInvalidError,
    OtpThrottledError,
    PhoneAlreadyRegisteredError,
    UserInactiveError,
)
from app.domain.services.authorization import AuthorizationService
from app.domain.value_objects.phone_number import normalize_phone

logger = logging.getLogger(__name__)

# ASCII on purpose: one GSM-7 segment, no Unicode surcharge.
DEFAULT_MESSAGE = "Folio: ma dang nhap cua ban la {code}. Ma het han sau {minutes} phut."
SIGNUP_MESSAGE = "Folio: ma dang ky tai khoan cua ban la {code}. Ma het han sau {minutes} phut."
# Users created from a phone number have no email; this placeholder keeps the NOT NULL/unique column happy.
SIGNUP_EMAIL_DOMAIN = "signup.folio.local"
DEFAULT_SIGNUP_ROLE = "user"


def _hash_code(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RequestOtpResult:
    expires_in: int


def _issue_code(
    otps: LoginOtpRepositoryPort,
    sms: SmsSenderPort,
    *,
    phone: str,
    user_id: Optional[UUID],
    now: datetime,
    ttl: int,
    resend_after: int,
    hourly_max: int,
    message: str,
) -> None:
    """Throttle per phone, void older codes, store the hash and send the SMS."""
    latest = otps.latest_for_phone(phone)
    if latest is not None and (now - latest.created_at) < timedelta(seconds=resend_after):
        raise OtpThrottledError("A code was sent recently; wait before asking again")
    if otps.count_created_since(phone, now - timedelta(hours=1)) >= hourly_max:
        raise OtpThrottledError("Too many codes requested; try again later")
    code = f"{secrets.randbelow(10**6):06d}"
    otps.void_active(phone, now)
    otps.save(
        LoginOtp(
            id=uuid4(),
            user_id=user_id,
            phone=phone,
            code_hash=_hash_code(phone, code),
            expires_at=now + timedelta(seconds=ttl),
            created_at=now,
        )
    )
    sms.send(phone, message.format(code=code, minutes=max(1, ttl // 60)))


def _consume_code(otps: LoginOtpRepositoryPort, *, phone: str, code: str, now: datetime, max_attempts: int) -> LoginOtp:
    """Return the matching active code (marked consumed) or raise ``OtpInvalidError``; wrong guesses count."""
    otp = otps.latest_for_phone(phone)
    if otp is None or not otp.is_active(now) or otp.attempts >= max_attempts:
        raise OtpInvalidError("Invalid or expired code")
    if not hmac.compare_digest(otp.code_hash, _hash_code(phone, code.strip())):
        otp.attempts += 1
        otps.save(otp)
        raise OtpInvalidError("Invalid or expired code")
    otp.consumed_at = now
    otps.save(otp)
    return otp


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

        _issue_code(
            self._otps,
            self._sms,
            phone=phone,
            user_id=user.id,
            now=now,
            ttl=self._ttl,
            resend_after=self._resend_after,
            hourly_max=self._hourly_max,
            message=self._message,
        )
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

    def execute(self, raw_phone: str, code: str, persistent: bool = False) -> LoginResult:
        phone = normalize_phone(raw_phone)
        now = self._clock()
        otp = _consume_code(self._otps, phone=phone, code=code, now=now, max_attempts=self._max_attempts)
        if otp.user_id is None:
            # A sign-up code cannot sign an existing account in.
            raise OtpInvalidError("Invalid or expired code")

        user = self._users.find_by_id(otp.user_id)
        if user is None or not user.is_active:
            raise UserInactiveError("User account is deactivated")
        permissions: List[str] = list(self._authz.get_user_permissions(user.id))
        return LoginResult(
            user_id=user.id,
            access_token=self._tokens.create_access_token(user.id, {"permissions": permissions}),
            refresh_token=self._tokens.create_refresh_token(user.id, persistent=persistent),
            permissions=permissions,
        )


class RequestSignupOtpUseCase:
    """Send a sign-up code to a phone that has no account yet."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        sms: SmsSenderPort,
        *,
        ttl_seconds: int = 300,
        resend_after_seconds: int = 60,
        hourly_max: int = 5,
        message: str = SIGNUP_MESSAGE,
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
        if self._users.find_by_phone(phone) is not None:
            raise PhoneAlreadyRegisteredError("This phone number already has an account")
        _issue_code(
            self._otps,
            self._sms,
            phone=phone,
            user_id=None,
            now=self._clock(),
            ttl=self._ttl,
            resend_after=self._resend_after,
            hourly_max=self._hourly_max,
            message=self._message,
        )
        return RequestOtpResult(expires_in=self._ttl)


class VerifySignupOtpUseCase:
    """Create the account (phone + display name) once the sign-up code checks out, and sign it in."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        otp_repo: LoginOtpRepositoryPort,
        role_repo: RoleRepositoryPort,
        password_hasher: PasswordHasherPort,
        authorization_service: AuthorizationService,
        token_issuer: TokenIssuerPort,
        *,
        max_attempts: int = 5,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._users = user_repo
        self._otps = otp_repo
        self._roles = role_repo
        self._hasher = password_hasher
        self._authz = authorization_service
        self._tokens = token_issuer
        self._max_attempts = max_attempts
        self._clock = clock

    def execute(self, raw_phone: str, code: str, display_name: str, persistent: bool = False) -> LoginResult:
        phone = normalize_phone(raw_phone)
        name = display_name.strip()
        if not name:
            raise ValueError("display_name is required")
        now = self._clock()
        otp = _consume_code(self._otps, phone=phone, code=code, now=now, max_attempts=self._max_attempts)
        if otp.user_id is not None:
            # A sign-in code cannot create an account.
            raise OtpInvalidError("Invalid or expired code")
        if self._users.find_by_phone(phone) is not None:
            raise PhoneAlreadyRegisteredError("This phone number already has an account")

        # No password: the account signs in by SMS code only. The hash is of a secret nobody knows.
        user = User.create(
            email=f"phone-{phone.lstrip('+')}@{SIGNUP_EMAIL_DOMAIN}",
            password_hash=self._hasher.hash(secrets.token_urlsafe(32)),
            display_name=name,
        )
        user.phone = phone
        self._users.save(user)
        default_role = self._roles.find_by_name(DEFAULT_SIGNUP_ROLE)
        if default_role is not None:
            self._users.assign_role(user.id, default_role.id)

        permissions: List[str] = list(self._authz.get_user_permissions(user.id))
        return LoginResult(
            user_id=user.id,
            access_token=self._tokens.create_access_token(user.id, {"permissions": permissions}),
            refresh_token=self._tokens.create_refresh_token(user.id, persistent=persistent),
            permissions=permissions,
        )
