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

    def sum_attempts_since(self, phone: str, since: datetime) -> int:
        """Wrong guesses made at the phone's codes created since ``since``, all purposes together.

        Every re-issued code starts again at zero attempts; this total does not.
        """
        ...

    def void_active(self, phone: str, now: datetime, purpose: OtpPurpose = OtpPurpose.SIGN_IN) -> None:
        """Mark every still-active code of the phone for ``purpose`` as consumed (a new one replaces them)."""
        ...

    def purge_stale(self, phone: str, before: datetime, batch_size: int) -> int:
        """Delete the phone's codes created before ``before``, plus at most ``batch_size`` other numbers' old ones.

        Retention for rows that otherwise stay forever (a phone number and a code hash). The caller picks
        a cutoff well beyond every window the throttles look at. Returns how many rows went.
        """
        ...

    def oldest_created_since(self, phone: str, since: datetime) -> Optional[datetime]:
        """Creation time of the phone's oldest code created since ``since`` (None if none)."""
        ...
