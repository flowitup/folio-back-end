"""The bell lists unread activity only: read entries leave the feed."""

from __future__ import annotations

from uuid import uuid4

from app.infrastructure.adapters.sqlalchemy_notification_event import SQLAlchemyNotificationEventRepository


def _record(repo, user_id, kind):
    repo.record([user_id], category="chat", kind=kind, texts={"vi": ["t", "b"]}, data={})


def test_read_entries_leave_the_feed(session):
    repo = SQLAlchemyNotificationEventRepository(session)
    user_id = uuid4()
    _record(repo, user_id, "a")
    _record(repo, user_id, "b")
    first, _ = sorted(repo.list_for_user(user_id), key=lambda e: e.kind)

    repo.mark_read(user_id, [first.id])
    assert [e.kind for e in repo.list_for_user(user_id)] == ["b"]
    assert repo.unread_count(user_id) == 1

    repo.mark_read(user_id)
    assert repo.list_for_user(user_id) == []
