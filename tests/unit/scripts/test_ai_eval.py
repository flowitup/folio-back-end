"""Unit tests for `scripts.ai_eval.run_eval` — the plan's M0 acceptance harness.

Only the scoring functions and the pass/fail exit-code logic are exercised: no network,
no real DeepSeek call. `run_invoice_eval`/`run_material_eval` are exercised end to end
against a fake `VisionLlmPort` and real gold.json/photo fixtures on disk (a temp dir), so
the file-discovery/gold-loading glue is covered too, not just the pure math.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, TypeVar

import pytest
from pydantic import BaseModel

from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.models import Invoice, MaterialIdent
from scripts.ai_eval.run_eval import (
    INVOICE_NUMBER_MIN_RATIO,
    TOTAL_TTC_MIN_RATIO,
    evaluate_acceptance,
    run_invoice_eval,
    run_material_eval,
    score_invoice_results,
    score_material_results,
)

T = TypeVar("T", bound=BaseModel)


class FakeVision:
    """Fake VisionLlmPort: returns one scripted answer per call, in order; raises
    `LlmOutputError` when the list is exhausted or a `None` sentinel is scripted (an
    extraction failure)."""

    def __init__(self, answers: list[Optional[BaseModel]]) -> None:
        self._answers = list(answers)
        self._index = 0

    def chat_json(
        self, system: str, user_text: str, images: list[bytes], model_cls: type[T], temperature: float = 0.0
    ) -> T:
        if self._index >= len(self._answers):
            raise LlmOutputError("FakeVision exhausted.")
        answer = self._answers[self._index]
        self._index += 1
        if answer is None:
            raise LlmOutputError("Scripted extraction failure.")
        assert isinstance(answer, model_cls)
        return answer

    def chat_text(self, system: str, user_text: str) -> str:  # pragma: no cover - unused by the eval harness
        raise NotImplementedError


def _invoice(**overrides: Any) -> Invoice:
    base = {
        "merchant": "Leroy Merlin",
        "invoice_number": "F-2026-0001",
        "date": "2026-09-10",
        "total_ttc": 79.54,
        "readability": 0.95,
    }
    base.update(overrides)
    return Invoice(**base)


def _material(**overrides: Any) -> MaterialIdent:
    base = {
        "name": "Perceuse à percussion Bosch",
        "brand": "Bosch",
        "reference": "GSB18V",
        "category": "outillage",
        "confidence": 0.9,
    }
    base.update(overrides)
    return MaterialIdent(**base)


# ---------------------------------------------------------------------------
# score_invoice_results
# ---------------------------------------------------------------------------


def test_score_invoice_results_all_fields_match() -> None:
    gold = {"total_ttc": 79.54, "invoice_number": "F-2026-0001", "date": "2026-09-10", "merchant": "Leroy Merlin"}
    tallies = score_invoice_results({"a.jpg": (_invoice(), gold)})
    assert tallies["total_ttc"].ratio() == 1.0
    assert tallies["invoice_number"].ratio() == 1.0
    assert tallies["date"].ratio() == 1.0
    assert tallies["merchant"].ratio() == 1.0


def test_score_invoice_results_total_ttc_within_tolerance() -> None:
    gold = {"total_ttc": 79.54, "invoice_number": "x", "date": "2026-09-10", "merchant": "x"}
    tallies = score_invoice_results({"a.jpg": (_invoice(total_ttc=79.542), gold)})
    assert tallies["total_ttc"].ratio() == 1.0


def test_score_invoice_results_total_ttc_outside_tolerance_fails() -> None:
    gold = {"total_ttc": 79.54, "invoice_number": "x", "date": "2026-09-10", "merchant": "x"}
    tallies = score_invoice_results({"a.jpg": (_invoice(total_ttc=79.60), gold)})
    assert tallies["total_ttc"].ratio() == 0.0
    assert tallies["total_ttc"].misses == ["a.jpg"]


def test_score_invoice_results_invoice_number_normalised() -> None:
    gold = {"total_ttc": 1.0, "invoice_number": "F 2026 0001", "date": "2026-09-10", "merchant": "x"}
    tallies = score_invoice_results({"a.jpg": (_invoice(invoice_number="f20260001"), gold)})
    assert tallies["invoice_number"].ratio() == 1.0


def test_score_invoice_results_merchant_case_insensitive_contains() -> None:
    gold = {"total_ttc": 1.0, "invoice_number": "x", "date": "2026-09-10", "merchant": "leroy merlin"}
    tallies = score_invoice_results({"a.jpg": (_invoice(merchant="LEROY MERLIN ARCUEIL"), gold)})
    assert tallies["merchant"].ratio() == 1.0


def test_score_invoice_results_extraction_failure_counts_as_a_miss_on_every_field() -> None:
    gold = {"total_ttc": 79.54, "invoice_number": "x", "date": "2026-09-10", "merchant": "x"}
    tallies = score_invoice_results({"a.jpg": (None, gold)})
    assert all(tally.ratio() == 0.0 for tally in tallies.values())


# ---------------------------------------------------------------------------
# score_material_results
# ---------------------------------------------------------------------------


def test_score_material_results_all_fields_match() -> None:
    gold = {"brand": "bosch", "reference": "gsb18v", "name_contains": "perceuse"}
    tallies = score_material_results({"a.jpg": (_material(), gold)})
    assert tallies["brand"].ratio() == 1.0
    assert tallies["reference"].ratio() == 1.0
    assert tallies["name_contains"].ratio() == 1.0


def test_score_material_results_mismatch() -> None:
    gold = {"brand": "makita", "reference": "gsb18v", "name_contains": "perceuse"}
    tallies = score_material_results({"a.jpg": (_material(), gold)})
    assert tallies["brand"].ratio() == 0.0


# ---------------------------------------------------------------------------
# evaluate_acceptance — the M0 pass/fail gate
# ---------------------------------------------------------------------------


def test_evaluate_acceptance_passes_at_exactly_the_threshold() -> None:
    gold_20 = {
        f"f{i}.jpg": {"total_ttc": 1.0, "invoice_number": "x", "date": None, "merchant": None} for i in range(20)
    }
    results = {
        name: (_invoice(total_ttc=1.0 if i < 19 else 2.0, invoice_number="x"), gold)
        for i, (name, gold) in enumerate(gold_20.items())
    }
    tallies = score_invoice_results(results)
    assert tallies["total_ttc"].ratio() == pytest.approx(TOTAL_TTC_MIN_RATIO)
    assert evaluate_acceptance(tallies) is True


def test_evaluate_acceptance_fails_just_below_total_ttc_threshold() -> None:
    gold_20 = {
        f"f{i}.jpg": {"total_ttc": 1.0, "invoice_number": "x", "date": None, "merchant": None} for i in range(20)
    }
    results = {
        name: (_invoice(total_ttc=1.0 if i < 18 else 2.0, invoice_number="x"), gold)
        for i, (name, gold) in enumerate(gold_20.items())
    }
    tallies = score_invoice_results(results)
    assert tallies["total_ttc"].ratio() < TOTAL_TTC_MIN_RATIO
    assert evaluate_acceptance(tallies) is False


def test_evaluate_acceptance_fails_just_below_invoice_number_threshold() -> None:
    gold_20 = {
        f"f{i}.jpg": {"total_ttc": 1.0, "invoice_number": "match", "date": None, "merchant": None} for i in range(20)
    }
    results = {
        name: (_invoice(total_ttc=1.0, invoice_number="match" if i < 16 else "nope"), gold)
        for i, (name, gold) in enumerate(gold_20.items())
    }
    tallies = score_invoice_results(results)
    assert tallies["invoice_number"].ratio() < INVOICE_NUMBER_MIN_RATIO
    assert evaluate_acceptance(tallies) is False


def test_evaluate_acceptance_trivially_passes_with_no_invoices() -> None:
    assert evaluate_acceptance({}) is True


# ---------------------------------------------------------------------------
# run_invoice_eval / run_material_eval — file discovery + gold loading
# ---------------------------------------------------------------------------


def test_run_invoice_eval_reads_gold_json_and_scores_every_file(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"fake-jpeg-bytes")
    (tmp_path / "b.jpg").write_bytes(b"fake-jpeg-bytes")
    (tmp_path / "gold.json").write_text(
        json.dumps(
            {
                "a.jpg": {"total_ttc": 79.54, "invoice_number": "F1", "date": "2026-09-10", "merchant": "Leroy"},
                "b.jpg": {"total_ttc": 12.0, "invoice_number": "F2", "date": "2026-09-11", "merchant": "Point P"},
            }
        )
    )
    vision = FakeVision([_invoice(total_ttc=79.54, invoice_number="F1"), _invoice(total_ttc=12.0, invoice_number="F2")])

    tallies = run_invoice_eval(vision, tmp_path, limit=None)

    assert tallies["total_ttc"].correct == 2
    assert tallies["total_ttc"].total == 2


def test_run_invoice_eval_missing_gold_json_exits_2(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"x")
    with pytest.raises(SystemExit) as exc_info:
        run_invoice_eval(FakeVision([]), tmp_path, limit=None)
    assert exc_info.value.code == 2


def test_run_invoice_eval_skips_files_with_no_gold_entry(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "untracked.jpg").write_bytes(b"x")
    (tmp_path / "gold.json").write_text(json.dumps({"a.jpg": {"total_ttc": 1.0, "invoice_number": "F1"}}))
    vision = FakeVision([_invoice(total_ttc=1.0, invoice_number="F1")])

    tallies = run_invoice_eval(vision, tmp_path, limit=None)

    assert tallies["total_ttc"].total == 1


def test_run_invoice_eval_respects_limit(tmp_path: Path) -> None:
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / name).write_bytes(b"x")
    gold = {name: {"total_ttc": 1.0, "invoice_number": "F"} for name in ("a.jpg", "b.jpg", "c.jpg")}
    (tmp_path / "gold.json").write_text(json.dumps(gold))
    vision = FakeVision([_invoice(total_ttc=1.0, invoice_number="F")])

    tallies = run_invoice_eval(vision, tmp_path, limit=1)

    assert tallies["total_ttc"].total == 1


def test_run_invoice_eval_extraction_failure_is_scored_as_a_miss(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "gold.json").write_text(json.dumps({"a.jpg": {"total_ttc": 1.0, "invoice_number": "F"}}))
    vision = FakeVision([None])

    tallies = run_invoice_eval(vision, tmp_path, limit=None)

    assert tallies["total_ttc"].correct == 0
    assert tallies["total_ttc"].total == 1


def test_run_material_eval_reads_gold_json_and_scores_every_file(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "gold.json").write_text(
        json.dumps({"a.jpg": {"brand": "bosch", "reference": "gsb18v", "name_contains": "perceuse"}})
    )
    vision = FakeVision([_material()])

    tallies = run_material_eval(vision, tmp_path, limit=None)

    assert tallies["brand"].correct == 1
