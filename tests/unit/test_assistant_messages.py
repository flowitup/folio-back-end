"""Unit tests for AssistantMessenger — body fallback rendering and post-commit push."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.assistant.exceptions import AssistantError
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
    product_id = uuid4()
    message = messenger.post_card(
        uuid4(),
        card_type="material",
        entity_id=product_id,
        title="Ciment Lafarge 25kg",
        subtitle="Confirmé",
        badge="confirmed",
    )
    assert message.content_type == "card"
    assert message.payload == {
        "card": {
            "type": "material",
            "id": str(product_id),
            "project_id": None,
            "title": "Ciment Lafarge 25kg",
            "subtitle": "Confirmé",
            "badge": "confirmed",
            "thumbnail_url": None,
            "extra": {},
        }
    }
    assert message.body == "Ciment Lafarge 25kg – Confirmé"


def test_post_card_fallback_without_subtitle_is_title_only() -> None:
    messenger, _, _ = _messenger()
    message = messenger.post_card(uuid4(), card_type="invoice", entity_id=uuid4(), title="Facture Leroy Merlin")
    assert message.body == "Facture Leroy Merlin"


def test_post_card_rejects_unknown_card_type() -> None:
    messenger, _, _ = _messenger()
    with pytest.raises(AssistantError):
        messenger.post_card(uuid4(), card_type="bogus", entity_id=uuid4(), title="x")


def test_post_card_rejects_absolute_thumbnail_url() -> None:
    messenger, _, _ = _messenger()
    with pytest.raises(AssistantError):
        messenger.post_card(
            uuid4(),
            card_type="material",
            entity_id=uuid4(),
            title="x",
            thumbnail_url="https://example.com/x.png",
        )


def test_post_card_thumbnail_url_and_project_id_and_extra_round_trip() -> None:
    messenger, _, _ = _messenger()
    product_id = uuid4()
    project_id = uuid4()
    message = messenger.post_card(
        uuid4(),
        card_type="invoice",
        entity_id=product_id,
        project_id=project_id,
        title="Facture Leroy Merlin",
        thumbnail_url="/api/v1/bibliotheque/products/x/image",
        extra={"invoice_number": "F-2026-0001", "total_ttc": 79.54},
    )
    assert message.payload["card"] == {
        "type": "invoice",
        "id": str(product_id),
        "project_id": str(project_id),
        "title": "Facture Leroy Merlin",
        "subtitle": None,
        "badge": None,
        "thumbnail_url": "/api/v1/bibliotheque/products/x/image",
        "extra": {"invoice_number": "F-2026-0001", "total_ttc": 79.54},
    }


def test_post_choice_fallback_numbers_the_options() -> None:
    messenger, _, _ = _messenger()
    user_id = uuid4()
    options = [
        {"label": "Confirmer", "action": "confirm", "payload": {}},
        {"label": "Annuler", "action": "cancel", "payload": {}},
    ]
    message = messenger.post_choice(user_id, "Confirmer le matériau ?", options)
    assert message.content_type == "choice"
    assert message.payload == {
        "prompt": "Confirmer le matériau ?",
        "options": options,
        "answered": None,
        "addressed_to": str(user_id),
    }
    assert message.body == "Confirmer le matériau ?\n1. Confirmer\n2. Annuler"


def test_post_choice_addressed_to_defaults_to_user_id() -> None:
    messenger, _, _ = _messenger()
    user_id = uuid4()
    message = messenger.post_choice(user_id, "OK ?", [{"label": "Oui", "action": "yes", "payload": {}}])
    assert message.payload["addressed_to"] == str(user_id)


def test_post_choice_addressed_to_can_be_overridden() -> None:
    """The asker (`user_id`, first arg) may differ from who the choice is addressed to
    — e.g. a company-channel choice the assistant asked on behalf of someone else."""
    messenger, _, _ = _messenger()
    user_id = uuid4()
    asker_id = uuid4()
    message = messenger.post_choice(
        user_id, "OK ?", [{"label": "Oui", "action": "yes", "payload": {}}], addressed_to=asker_id
    )
    assert message.payload["addressed_to"] == str(asker_id)


def test_post_text_targets_the_given_channel_instead_of_the_assistant_fallback() -> None:
    from app.domain.entities.chat_message import ChannelRef

    messenger, _, _ = _messenger()
    channel = ChannelRef(kind="company", id=uuid4())
    message = messenger.post_text(uuid4(), "Bonjour l'équipe", channel=channel)
    assert message.channel == channel


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
