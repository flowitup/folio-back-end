"""Unit tests for `app.application.assistant.reply` — language detection + templates."""

from __future__ import annotations

import pytest

from app.application.assistant import reply
from app.application.assistant.equipment import EquipmentHit
from app.application.assistant.models import INTENTS
from uuid import uuid4

# ---------------------------------------------------------------------------
# Language detection table
# ---------------------------------------------------------------------------

_LANG_CASES: list[tuple[str, str | None, str]] = [
    # (text, hint, expected)
    ("Xin chào, máy cắt gạch ở đâu?", None, "vi"),
    ("Cảm ơn bạn nhiều", None, "vi"),
    ("đục bê tông", None, "vi"),
    ("Bonjour, où est la perceuse ?", None, "fr"),
    ("Merci pour le chantier", None, "fr"),
    ("Cherche la facture Leroy Merlin", None, "fr"),
    ("Hello, where is the drill?", None, "en"),
    ("Thanks for the update", None, "en"),
    ("", None, "en"),
    # The app's explicit lang hint always wins, even against contradicting text.
    ("Hello there", "vi", "vi"),
    ("Bonjour", "en", "en"),
    ("random gibberish", "not-a-lang", "en"),
]


@pytest.mark.parametrize("text,hint,expected", _LANG_CASES)
def test_detect_lang(text: str, hint: str | None, expected: str) -> None:
    assert reply.detect_lang(text, hint) == expected


def test_render_fixed_template() -> None:
    assert reply.render("greeting", "fr") == reply.TEMPLATES["greeting"]["fr"]
    assert reply.render("greeting", "vi") == reply.TEMPLATES["greeting"]["vi"]
    assert reply.render("greeting", "en") == reply.TEMPLATES["greeting"]["en"]


def test_render_unknown_lang_falls_back_to_default() -> None:
    assert reply.render("greeting", "de") == reply.TEMPLATES["greeting"][reply._DEFAULT_LANG]


def test_render_formats_kwargs() -> None:
    text = reply.render("equipment_moved", "fr", name="Perceuse Bosch", project="Villa Arcueil")
    assert text == "Perceuse Bosch déplacé vers Villa Arcueil."


def test_every_template_has_all_three_languages() -> None:
    for key, translations in reply.TEMPLATES.items():
        assert set(translations) == set(reply.LANGUAGES), f"{key} is missing a language"


def test_intent_labels_cover_every_intent_and_language() -> None:
    assert set(reply.INTENT_LABELS) == set(INTENTS)
    for intent, translations in reply.INTENT_LABELS.items():
        assert set(translations) == set(reply.LANGUAGES), f"{intent} is missing a language"


def test_intent_label_unknown_intent_returns_the_raw_value() -> None:
    assert reply.intent_label("does_not_exist", "fr") == "does_not_exist"


def test_render_equipment_found_joins_one_line_per_hit() -> None:
    hits = [
        EquipmentHit(
            item_id=uuid4(),
            company_id=uuid4(),
            name="Perceuse Bosch",
            quantity=2,
            condition="working",
            location_label="Entrepôt principal",
            location_type="warehouse",
            warehouse_id=uuid4(),
            project_id=None,
        ),
        EquipmentHit(
            item_id=uuid4(),
            company_id=uuid4(),
            name="Carrelette",
            quantity=1,
            condition="damaged",
            location_label="Villa Arcueil",
            location_type="site",
            warehouse_id=None,
            project_id=uuid4(),
        ),
    ]
    text = reply.render_equipment_found(hits, "fr")
    lines = text.splitlines()
    assert len(lines) == 2
    assert "Perceuse Bosch" in lines[0] and "Entrepôt principal" in lines[0]
    assert "Carrelette" in lines[1] and "Villa Arcueil" in lines[1]


@pytest.mark.parametrize(
    "text",
    ["bonjour", "Bonjour!", "salut", "hello", "Hi", "xin chào", "chào", " Hey "],
)
def test_is_trivial_greeting(text: str) -> None:
    assert reply.is_trivial_greeting(text) is True


@pytest.mark.parametrize("text", ["bonjour tout le monde", "hello, where is the drill?", ""])
def test_is_trivial_greeting_false_for_longer_messages(text: str) -> None:
    assert reply.is_trivial_greeting(text) is False


def test_chit_chat_reply_uses_the_language_specific_system_prompt() -> None:
    class RecordingVision:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def chat_json(self, system, user_text, images, model_cls, temperature=0.0):  # pragma: no cover - unused
            raise AssertionError("chat_json should not be called by chit_chat_reply")

        def chat_text(self, system: str, user_text: str) -> str:
            self.calls.append((system, user_text))
            return "reply"

    vision = RecordingVision()
    result = reply.chit_chat_reply(vision, "vi", "Chào bạn")
    assert result == "reply"
    assert vision.calls == [(reply._CHAT_SYSTEM_VI, "Chào bạn")]
