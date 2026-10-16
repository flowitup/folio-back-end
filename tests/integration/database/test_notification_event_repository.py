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


def _event(repo, user_id, kind, **data):
    repo.record([user_id], category="x", kind=kind, texts={"vi": ["t", "b"]}, data={"kind": kind, **data})


def test_attendance_requests_are_left_to_the_to_validate_section(session):
    repo = SQLAlchemyNotificationEventRepository(session)
    user_id = uuid4()
    _event(repo, user_id, "submitted", entry_id="e1")
    _event(repo, user_id, "change_requested", entry_id="e2")
    _event(repo, user_id, "validated", entry_id="e3")
    assert [e.kind for e in repo.list_for_user(user_id)] == ["validated"]
    assert repo.unread_count(user_id) == 1


def test_entries_about_a_closed_project_or_company_are_hidden_except_the_removal(session):
    repo = SQLAlchemyNotificationEventRepository(session)
    user_id = uuid4()
    _event(repo, user_id, "task_assigned", project_id="p-gone", task_id="t1")
    _event(repo, user_id, "project_member_removed", project_id="p-gone")
    _event(repo, user_id, "task_assigned", project_id="p-open", task_id="t2")
    _event(repo, user_id, "member_added", company_id="c-gone")
    _event(repo, user_id, "chat_message", channel_key="company:c1")
    projects, companies = repo.unread_scopes(user_id)
    assert projects == {"p-gone", "p-open"} and companies == {"c-gone"}
    kinds = sorted(e.kind for e in repo.list_for_user(user_id, hidden_projects=["p-gone"], hidden_companies=["c-gone"]))
    assert kinds == ["chat_message", "project_member_removed", "task_assigned"]
    assert repo.unread_count(user_id, hidden_projects=["p-gone"], hidden_companies=["c-gone"]) == 3


def test_mark_read_matching_and_delete_matching(session):
    repo = SQLAlchemyNotificationEventRepository(session)
    me, other = uuid4(), uuid4()
    _event(repo, me, "chat_message", channel_key="project:1")
    _event(repo, me, "chat_message", channel_key="project:2")
    _event(repo, other, "chat_message", channel_key="project:1")
    assert repo.mark_read_matching(kinds=["chat_message"], match={"channel_key": "project:1"}, user_ids=[me]) == 1
    assert [e.data["channel_key"] for e in repo.list_for_user(me)] == ["project:2"]
    assert repo.unread_count(other) == 1

    _event(repo, other, "invoice_created", project_id="p9", actor_id="u1")
    assert repo.delete_matching(match={"project_id": "p9"}) == 1
    assert [e.kind for e in repo.list_for_user(other)] == ["chat_message"]
