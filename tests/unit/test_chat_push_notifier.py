"""Unit tests for ChatPushNotifier — coalescing, recipients, and never breaking a send."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.application.push.chat_push_notifier import ChatPushNotifier
from app.application.push.dispatcher import PushDispatcher
from app.domain.entities.chat_message import ChannelRef

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Member:
    id: UUID
    name: str


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, messages, on_invalid_token=None):
        self.sent.extend(messages)


class StubDevices:
    def __init__(self, users) -> None:
        self._users = users

    def tokens_for_users(self, user_ids):
        return {u: [f"tok-{u}"] for u in user_ids if u in self._users}

    def delete_token(self, token):
        pass


class StubDirectory:
    def __init__(self, members) -> None:
        self._members = members

    def list_members(self, channel):
        return self._members

    def display_names(self, user_ids):
        return {m.id: m.name for m in self._members if m.id in user_ids}

    def channel_name(self, channel):
        return "Chantier Arcueil"


class StubReads:
    """Doubles as the read repo and the message repo (both protocols are tiny)."""

    def __init__(self, unread_by_user=None) -> None:
        self._unread = unread_by_user or {}

    def last_reads_for_channel(self, channel):
        return {}

    def count_since(self, channel, since, exclude_sender):
        return self._unread.get(exclude_sender, 1)


class InMemoryMarkers:
    def __init__(self) -> None:
        self.marks: dict = {}

    def due_recipients(self, user_ids, channel_key, window_seconds, now):
        cutoff = now - timedelta(seconds=window_seconds)
        return [u for u in user_ids if self.marks.get((u, channel_key), cutoff) <= cutoff]

    def mark_notified(self, user_ids, channel_key, now):
        for u in user_ids:
            self.marks[(u, channel_key)] = now


def _build(members, unread=None, markers=None):
    sender = RecordingSender()
    dispatcher = PushDispatcher(
        devices=StubDevices({m.id for m in members}), sender=sender, locale="en", run_async=False
    )
    directory = StubDirectory(members)
    reads = StubReads(unread)
    notifier = ChatPushNotifier(
        dispatcher=dispatcher,
        directory=directory,
        markers=markers or InMemoryMarkers(),
        reads=reads,
        messages=reads,
        names=directory,
        window_seconds=60,
    )
    return notifier, sender


CHANNEL = ChannelRef(kind="project", id=uuid4())


def test_notifies_every_member_except_the_sender():
    alice, bob, carol = uuid4(), uuid4(), uuid4()
    members = [Member(alice, "Alice"), Member(bob, "Bob"), Member(carol, "Carol")]
    notifier, sender = _build(members)
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="Bonjour", sent_at=NOW)
    assert sorted(m.token for m in sender.sent) == sorted([f"tok-{bob}", f"tok-{carol}"])


def test_second_message_inside_the_window_is_coalesced_away():
    alice, bob = uuid4(), uuid4()
    markers = InMemoryMarkers()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], markers=markers)
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="one", sent_at=NOW)
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="two", sent_at=NOW + timedelta(seconds=30))
    assert len(sender.sent) == 1


def test_message_after_the_window_notifies_again():
    alice, bob = uuid4(), uuid4()
    markers = InMemoryMarkers()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], markers=markers)
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="one", sent_at=NOW)
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="two", sent_at=NOW + timedelta(seconds=90))
    assert len(sender.sent) == 2


def test_body_carries_the_preview_and_the_unread_count():
    alice, bob = uuid4(), uuid4()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], unread={bob: 4})
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="Livraison demain", sent_at=NOW)
    assert sender.sent[0].title == "Alice · Chantier Arcueil"
    assert sender.sent[0].body == "Livraison demain (+3 unread)"


def test_single_unread_body_has_no_count_suffix():
    alice, bob = uuid4(), uuid4()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], unread={bob: 1})
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="Salut", sent_at=NOW)
    assert sender.sent[0].body == "Salut"


def test_image_only_message_gets_a_placeholder_body():
    alice, bob = uuid4(), uuid4()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], unread={bob: 1})
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview=None, sent_at=NOW)
    assert sender.sent[0].body == "Sent an image"


def test_payload_routes_to_the_channel():
    alice, bob = uuid4(), uuid4()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")])
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="x", sent_at=NOW)
    assert sender.sent[0].data == {"kind": "chat_message", "channel_key": CHANNEL.key}


def test_lone_member_channel_sends_nothing():
    alice = uuid4()
    notifier, sender = _build([Member(alice, "Alice")])
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="x", sent_at=NOW)
    assert sender.sent == []


def test_a_failure_in_the_notifier_never_breaks_the_send():
    class ExplodingDirectory(StubDirectory):
        def list_members(self, channel):
            raise RuntimeError("directory down")

    sender = RecordingSender()
    dispatcher = PushDispatcher(devices=StubDevices(set()), sender=sender, locale="en", run_async=False)
    notifier = ChatPushNotifier(
        dispatcher=dispatcher,
        directory=ExplodingDirectory([]),
        markers=InMemoryMarkers(),
        reads=StubReads(),
        messages=StubReads(),
        names=StubDirectory([]),
    )
    notifier.message_sent(channel=CHANNEL, sender_id=uuid4(), preview="x", sent_at=NOW)  # must not raise
    assert sender.sent == []


def test_long_preview_is_truncated():
    alice, bob = uuid4(), uuid4()
    notifier, sender = _build([Member(alice, "Alice"), Member(bob, "Bob")], unread={bob: 1})
    notifier.message_sent(channel=CHANNEL, sender_id=alice, preview="x" * 500, sent_at=NOW)
    assert len(sender.sent[0].body) == 120
