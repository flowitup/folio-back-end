"""Unit tests for `app.application.assistant.gate` — every threshold helper, no I/O."""

from __future__ import annotations

from app.application.assistant import gate


def test_intent_status_thresholds() -> None:
    assert gate.intent_status(gate.INTENT_AUTO) == "confirmed"
    assert gate.intent_status(gate.INTENT_AUTO + 0.01) == "confirmed"
    assert gate.intent_status(gate.INTENT_AUTO - 0.01) == "to_confirm"


def test_readability_ok_threshold() -> None:
    assert gate.readability_ok(gate.READABILITY_MIN) is True
    assert gate.readability_ok(gate.READABILITY_MIN - 0.01) is False


def test_duplicate_status_three_bands() -> None:
    assert gate.duplicate_status(gate.DUP_REJECT) == "reject"
    assert gate.duplicate_status(gate.DUP_ASK_LOW) == "ask"
    assert gate.duplicate_status(gate.DUP_ASK_LOW - 0.01) == "none"


def test_project_status_three_bands() -> None:
    assert gate.project_status(gate.PROJECT_CONFIRMED) == "confirmed"
    assert gate.project_status(gate.PROJECT_ASK_LOW) == "to_confirm"
    assert gate.project_status(gate.PROJECT_ASK_LOW - 0.01) == "needs_review"


def test_amounts_ok_threshold() -> None:
    assert gate.amounts_ok(gate.AMOUNTS_OK) is True
    assert gate.amounts_ok(gate.AMOUNTS_OK - 0.01) is False


def test_verify_faithful_threshold() -> None:
    assert gate.verify_faithful(gate.VERIFY_FAITHFUL) is True
    assert gate.verify_faithful(gate.VERIFY_FAITHFUL - 0.01) is False


def test_attach_existing_allowed_threshold() -> None:
    assert gate.attach_existing_allowed(gate.ATTACH_EXISTING) is True
    assert gate.attach_existing_allowed(gate.ATTACH_EXISTING - 0.01) is False


def test_pick_status_three_bands() -> None:
    assert gate.pick_status(gate.PICK_CONFIRMED) == "confirmed"
    assert gate.pick_status(gate.PICK_ASK_LOW) == "to_confirm"
    assert gate.pick_status(gate.PICK_ASK_LOW - 0.01) == "reject"


def test_is_write_allowed_threshold() -> None:
    assert gate.is_write_allowed(gate.IS_WRITE) is True
    assert gate.is_write_allowed(gate.IS_WRITE - 0.01) is False
