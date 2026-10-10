"""Persistence port for SMS codes (sign-in / sign-up and phone-number change)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from app.domain.entities.login_otp import LoginOtp, OtpPurpose


class LoginOtpRepositoryPort(Protocol):
    def save(self, otp: LoginOtp) -> None: ...

    def latest_for_phone(
        self, phone: str, purpose: Optional[OtpPurpose] = None, *, for_update: bool = False
    ) -> Optional[LoginOtp]:
        """Most recently created code for the phone, consumed or not.

        Restricted to ``purpose`` when given; ``None`` means any purpose (used by the resend throttle,
        which counts every SMS sent to a number whatever it was for). ``for_update`` locks the row until
        the transaction ends and reads its committed state, so concurrent checks of one code run one
        after another and every wrong guess counts.
        """
        ...

    def count_created_since(self, phone: str, since: datetime) -> int:
        """Codes created for the phone since ``since``, all purposes together."""
        ...

    def void_active(self, phone: str, now: datetime, purpose: OtpPurpose = OtpPurpose.SIGN_IN) -> None:
        """Mark every still-active code of the phone for ``purpose`` as consumed (a new one replaces them)."""
        ...

    def oldest_created_since(self, phone: str, since: datetime) -> Optional[datetime]:
        """Creation time of the phone's oldest code created since ``since`` (None if none)."""
        ...
