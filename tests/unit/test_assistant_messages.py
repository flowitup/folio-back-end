"""Unit tests for AssistantMessenger — body fallback rendering and post-commit push."""

from __future__ import annotations

from uuid import uuid4

from app.application.assistant.messages import AssistantMessenger
from app.domain.entities.chat_message import ChatMessage


class FakeMessageRepo:
    def __init__(self) -> None:
        self.added: list[ChatMessage] = []

    def add(self, message: ChatMessage) -> None:
        self.added.append(message)

    def find_by_id(self, message_id):
        return next((m for m in self.added if m.id == message_id), None)

    def update_payload(self, message_id, payload) -> None:
        raise NotImplementedError


class FakeDbSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def message_sent_by_assistant(self, *, channel, preview, sent_at) -> None:
        self.calls.append((channel, preview, sent_at))


def _messenger():
    repo = FakeMessageRepo()
    db = FakeDbSession()
    return AssistantMessenger(repo, db), repo, db


def test_post_text_persists_and_commits() -> None:
    messenger, repo, db = _messenger()
    user_id = uuid4()
    message = messenger.post_text(user_id, "Bonjour !")
    assert repo.added == [message]
    assert db.commits == 1
    assert message.sender_id is None
    assert message.sender_type == "assistant"
    assert message.content_type == "text"
    assert message.body == "Bonjour !"
    assert message.channel.kind == "assistant"
    assert message.channel.id == user_id


def test_post_text_notifies_after_commit() -> None:
    messenger, _, _ = _messenger()
    notifier = RecordingNotifier()
    messenger.notifier = notifier
    message = messenger.post_text(uuid4(), "Salut")
    assert len(notifier.calls) == 1
    channel, preview, sent_at = notifier.calls[0]
    assert channel == message.channel
    assert preview == "Salut"
    assert sent_at == message.created_at


def test_post_card_fallback_is_title_dash_subtitle() -> None:
    messenger, _, _ = _messenger()
    card = {
        "type": "material",
        "id": str(uuid4()),
        "project_id": None,
        "title": "Ciment Lafarge 25kg",
        "subtitle": "Confirmé",
        "badge": "confirmed",
        "thumbnail_url": None,
    }
    message = messenger.post_card(uuid4(), card)
    assert message.content_type == "card"
    assert message.payload == {"card": card}
    assert message.body == "Ciment Lafarge 25kg – Confirmé"


def test_post_card_fallback_without_subtitle_is_title_only() -> None:
    messenger, _, _ = _messenger()
    card = {"type": "invoice", "id": str(uuid4()), "project_id": None, "title": "Facture Leroy Merlin"}
    message = messenger.post_card(uuid4(), card)
    assert message.body == "Facture Leroy Merlin"


def test_post_choice_fallback_numbers_the_options() -> None:
    messenger, _, _ = _messenger()
    options = [
        {"label": "Confirmer", "action": "confirm", "payload": {}},
        {"label": "Annuler", "action": "cancel", "payload": {}},
    ]
    message = messenger.post_choice(uuid4(), "Confirmer le matériau ?", options)
    assert message.content_type == "choice"
    assert message.payload == {"prompt": "Confirmer le matériau ?", "options": options, "answered": None}
    assert message.body == "Confirmer le matériau ?\n1. Confirmer\n2. Annuler"


def test_post_job_status_fallback_is_the_status_text() -> None:
    messenger, _, _ = _messenger()
    message = messenger.post_job_status(uuid4(), "job-123", "running", "Récupération de la facture…", progress=0.5)
    assert message.content_type == "job_status"
    assert message.payload == {
        "job_id": "job-123",
        "state": "running",
        "text": "Récupération de la facture…",
        "progress": 0.5,
    }
    assert message.body == "Récupération de la facture…"


def test_reply_to_id_and_trace_id_are_forwarded() -> None:
    messenger, _, _ = _messenger()
    reply_to = uuid4()
    message = messenger.post_text(uuid4(), "Photo reçue.", reply_to_id=reply_to, trace_id="trace-1")
    assert message.reply_to_id == reply_to
    assert message.ai_trace_id == "trace-1"
