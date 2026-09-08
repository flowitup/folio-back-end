"""Integration tests for SQLAlchemyChatPushMarkerRepository against SQLite.

The quiet-window decision is SQL, so the in-memory stub used by the notifier unit tests
cannot prove it: this covers the real filter and the insert/update split in mark_notified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.infrastructure.adapters.sqlalchemy_chat_push_marker import SQLAlchemyChatPushMarkerRepository
from app.infrastructure.database.models import UserModel

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
CHANNEL = f"project:{uuid4()}"
WINDOW = 60


def _user(session, email: str):
    user = UserModel(email=email, password_hash="x", is_active=True)
    session.add(user)
    session.flush()
    return user


def test_a_user_never_notified_is_due(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"never-{uuid4()}@marker.test")
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW) == [user.id]


def test_a_user_notified_inside_the_window_is_not_due(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"recent-{uuid4()}@marker.test")
    repo.mark_notified([user.id], CHANNEL, NOW)
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW + timedelta(seconds=30)) == []


def test_a_user_notified_before_the_window_is_due_again(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"stale-{uuid4()}@marker.test")
    repo.mark_notified([user.id], CHANNEL, NOW)
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW + timedelta(seconds=90)) == [user.id]


def test_the_window_is_per_channel(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"perchannel-{uuid4()}@marker.test")
    other_channel = f"company:{uuid4()}"
    repo.mark_notified([user.id], CHANNEL, NOW)
    assert repo.due_recipients([user.id], other_channel, WINDOW, NOW) == [user.id]


def test_mark_notified_updates_an_existing_row_instead_of_duplicating(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"update-{uuid4()}@marker.test")
    repo.mark_notified([user.id], CHANNEL, NOW)
    repo.mark_notified([user.id], CHANNEL, NOW + timedelta(seconds=90))
    # Still one row (a duplicate would violate the composite PK), and the window now runs
    # from the second call: quiet until NOW+150, due after it.
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW + timedelta(seconds=120)) == []
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW + timedelta(seconds=160)) == [user.id]


def test_mixed_batch_returns_only_the_due_users(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    fresh = _user(session, f"fresh-{uuid4()}@marker.test")
    quiet = _user(session, f"quiet-{uuid4()}@marker.test")
    repo.mark_notified([quiet.id], CHANNEL, NOW)
    due = repo.due_recipients([fresh.id, quiet.id], CHANNEL, WINDOW, NOW + timedelta(seconds=10))
    assert due == [fresh.id]


def test_empty_input_is_a_no_op(session):
    repo = SQLAlchemyChatPushMarkerRepository(session)
    assert repo.due_recipients([], CHANNEL, WINDOW, NOW) == []
    repo.mark_notified([], CHANNEL, NOW)  # must not raise


def test_a_lost_insert_race_leaves_the_session_usable(session, monkeypatch):
    """Two senders hit the same channel at once: the loser's commit fails on the PK.

    The marker already exists, so the failure is harmless — but the shared session must be
    rolled back, or the send request that already committed its message blows up on its
    next query.
    """
    from sqlalchemy.exc import IntegrityError

    repo = SQLAlchemyChatPushMarkerRepository(session)
    user = _user(session, f"race-{uuid4()}@marker.test")
    real_commit = session.commit

    def failing_commit():
        monkeypatch.setattr(session, "commit", real_commit)
        raise IntegrityError("INSERT INTO chat_push_markers", {}, Exception("duplicate key"))

    monkeypatch.setattr(session, "commit", failing_commit)
    repo.mark_notified([user.id], CHANNEL, NOW)  # must not raise
    # The session still answers queries afterwards.
    assert repo.due_recipients([user.id], CHANNEL, WINDOW, NOW) == [user.id]
