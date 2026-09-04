"""One-time login code sent by SMS to a user's phone."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LoginOtp:
    id: UUID
    user_id: UUID
    phone: str
    # SHA-256 of "<phone>:<code>"; the clear code only ever lives in the SMS.
    code_hash: str
    expires_at: datetime
    created_at: datetime
    attempts: int = 0
    consumed_at: Optional[datetime] = None

    def is_active(self, now: datetime) -> bool:
        return self.consumed_at is None and now < self.expires_at
