"""Persistence port for SMS login codes."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from app.domain.entities.login_otp import LoginOtp


class LoginOtpRepositoryPort(Protocol):
    def save(self, otp: LoginOtp) -> None: ...

    def latest_for_phone(self, phone: str) -> Optional[LoginOtp]:
        """Most recently created code for the phone, consumed or not."""
        ...

    def count_created_since(self, phone: str, since: datetime) -> int: ...

    def void_active(self, phone: str, now: datetime) -> None:
        """Mark every still-active code of the phone as consumed (a new one replaces them)."""
        ...
