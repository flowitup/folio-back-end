"""SQLAlchemyLoginOtpRepository: the locked read that keeps the wrong-code limit honest.

Ten wrong guesses sent at once all read ``attempts`` before any of them wrote it back, so the counter
ended far below ten and the real code still worked. The check now reads the code under a row lock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event, update
from sqlalchemy.dialects import postgresql

from app.domain.entities.login_otp import LoginOtp, OtpPurpose
from app.infrastructure.adapters.sqlalchemy_login_otp import SQLAlchemyLoginOtpRepository
from app.infrastructure.database.models.login_otp import LoginOtpOrm

PHONE = "+33620100999"
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)


def _seed(repo: SQLAlchemyLoginOtpRepository) -> LoginOtp:
    otp = LoginOtp(
        id=uuid4(),
        user_id=None,
        phone=PHONE,
        code_hash="x",
        expires_at=NOW + timedelta(minutes=5),
        created_at=NOW,
        purpose=OtpPurpose.SIGN_IN,
    )
    repo.save(otp)
    return otp


def test_locked_read_is_select_for_update_on_postgres(session):
    repo = SQLAlchemyLoginOtpRepository(session)
    _seed(repo)
    statements = []
    listener = lambda state: statements.append(state.statement)  # noqa: E731
    event.listen(session, "do_orm_execute", listener)
    try:
        repo.latest_for_phone(PHONE, OtpPurpose.SIGN_IN, for_update=True)
        repo.latest_for_phone(PHONE, OtpPurpose.SIGN_IN)
    finally:
        event.remove(session, "do_orm_execute", listener)
    locked, plain = (str(s.compile(dialect=postgresql.dialect())) for s in statements)
    assert locked.rstrip().endswith("FOR UPDATE")
    assert "FOR UPDATE" not in plain


def test_locked_read_sees_the_counter_another_request_committed(session):
    """The request that waited on the lock must count on top of the other one's guess, not overwrite it."""
    repo = SQLAlchemyLoginOtpRepository(session)
    otp = _seed(repo)
    cached = session.get(LoginOtpOrm, otp.id)  # the session keeps its own copy of the row
    # Another request's wrong guess, written behind this session's back.
    session.execute(
        update(LoginOtpOrm)
        .where(LoginOtpOrm.id == otp.id)
        .values(attempts=3)
        .execution_options(synchronize_session=False)
    )
    assert repo.latest_for_phone(PHONE, OtpPurpose.SIGN_IN).attempts == 0  # a plain read trusts the copy

    fresh = repo.latest_for_phone(PHONE, OtpPurpose.SIGN_IN, for_update=True)
    assert fresh.attempts == 3
    fresh.attempts += 1
    repo.save(fresh)
    assert cached.attempts == 4


def test_sum_attempts_since_adds_the_wrong_guesses_of_every_code_in_the_window(session):
    """A re-issued code starts at zero attempts; the phone's total must still add up."""
    repo = SQLAlchemyLoginOtpRepository(session)

    def code(phone, minutes_ago, attempts, purpose=OtpPurpose.SIGN_IN):
        repo.save(
            LoginOtp(
                id=uuid4(),
                user_id=None,
                phone=phone,
                code_hash="x",
                expires_at=NOW + timedelta(minutes=5),
                created_at=NOW - timedelta(minutes=minutes_ago),
                attempts=attempts,
                purpose=purpose,
            )
        )

    code(PHONE, 5, 5)
    code(PHONE, 30, 3, OtpPurpose.PHONE_CHANGE)
    code(PHONE, 90, 5)  # older than the hour
    code("+33620100998", 5, 4)  # another phone
    assert repo.sum_attempts_since(PHONE, NOW - timedelta(hours=1)) == 8
    assert repo.sum_attempts_since(PHONE, NOW - timedelta(hours=2)) == 13
    assert repo.sum_attempts_since("+33620100997", NOW - timedelta(hours=1)) == 0
