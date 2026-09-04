"""SQLAlchemy implementation of ``LoginOtpRepositoryPort``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.entities.login_otp import LoginOtp
from app.infrastructure.database.models.login_otp import LoginOtpOrm


def _aware(value: datetime) -> datetime:
    # SQLite drops tzinfo; every stored timestamp is UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _to_entity(row: LoginOtpOrm) -> LoginOtp:
    return LoginOtp(
        id=row.id,
        user_id=row.user_id,
        phone=row.phone,
        code_hash=row.code_hash,
        expires_at=_aware(row.expires_at),
        created_at=_aware(row.created_at),
        attempts=row.attempts,
        consumed_at=_aware(row.consumed_at) if row.consumed_at else None,
    )


class SQLAlchemyLoginOtpRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, otp: LoginOtp) -> None:
        row = self._session.get(LoginOtpOrm, otp.id)
        if row is None:
            row = LoginOtpOrm(id=otp.id, user_id=otp.user_id, phone=otp.phone, created_at=otp.created_at)
            self._session.add(row)
        row.code_hash = otp.code_hash
        row.expires_at = otp.expires_at
        row.attempts = otp.attempts
        row.consumed_at = otp.consumed_at
        self._session.flush()

    def latest_for_phone(self, phone: str) -> Optional[LoginOtp]:
        row = self._session.execute(
            select(LoginOtpOrm).where(LoginOtpOrm.phone == phone).order_by(LoginOtpOrm.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        return _to_entity(row) if row is not None else None

    def count_created_since(self, phone: str, since: datetime) -> int:
        count = self._session.execute(
            select(func.count())
            .select_from(LoginOtpOrm)
            .where(LoginOtpOrm.phone == phone, LoginOtpOrm.created_at >= since)
        ).scalar_one()
        return int(count)

    def void_active(self, phone: str, now: datetime) -> None:
        self._session.execute(
            update(LoginOtpOrm)
            .where(LoginOtpOrm.phone == phone, LoginOtpOrm.consumed_at.is_(None))
            .values(consumed_at=now)
        )
        self._session.flush()
