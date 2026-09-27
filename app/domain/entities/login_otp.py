"""One-time code sent by SMS to a phone: sign-in / sign-up, or proving a new number."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID


class OtpPurpose(str, Enum):
    """What a code may be used for. A code only ever works for the purpose it was issued for."""

    # Sign-in (user_id set) and sign-up / invitation acceptance (user_id None).
    SIGN_IN = "sign_in"
    # A signed-in user proving they hold the new number they want to sign in with.
    PHONE_CHANGE = "phone_change"


@dataclass(slots=True)
class LoginOtp:
    id: UUID
    # None for sign-up codes: the account does not exist until the code is verified.
    user_id: Optional[UUID]
    phone: str
    # SHA-256 of "<phone>:<code>"; the clear code only ever lives in the SMS.
    code_hash: str
    expires_at: datetime
    created_at: datetime
    attempts: int = 0
    consumed_at: Optional[datetime] = None
    purpose: OtpPurpose = OtpPurpose.SIGN_IN

    def is_active(self, now: datetime) -> bool:
        return self.consumed_at is None and now < self.expires_at
