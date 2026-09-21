"""The plan's M0 acceptance harness — S1 (invoice extraction) and A1 (material
identification) accuracy against a hand-labelled gold set.

    uv run python -m scripts.ai_eval.run_eval --invoices eval/invoices --materials eval/materials [--limit N]

Runs S1 (`app.application.assistant.extract.extract_invoice`) on every
``*.pdf|*.jpg|*.jpeg|*.png`` in ``--invoices`` and compares it against that directory's
``gold.json``; runs A1 (the exact prompt `features/material.py` uses for `MaterialIdent`)
on every photo in ``--materials`` against its own ``gold.json``. Prints a per-field
accuracy table for both, then exits 1 when the plan's M0 acceptance bar is not met:
``total_ttc`` exact match (±0.005) on at least 19/20 (95%) of the invoice set, and
``invoice_number`` (normalised: strip spaces, case-insensitive) on at least 17/20 (85%)
— both ratios scaled to however many files are actually present. See ``eval/README.md``
for the gold format and how to build a real eval set (this repo ships none — a photo/PDF
of a real invoice cannot be committed).

Needs a real ``DEEPSEEK_API_KEY`` (exits 2 with a clear message otherwise) — this makes
real, billed network calls, so it is never run in CI, only by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.application.assistant.exceptions import LlmOutputError, ProviderNotConfiguredError
from app.application.assistant.extract import extract_invoice, pdf_to_images
from app.application.assistant.features.material import IDENTIFY_SYSTEM_FR
from app.application.assistant.features.material import _IDENTIFY_USER_TEXT as IDENTIFY_USER_TEXT
from app.application.assistant.models import Invoice, MaterialIdent
from app.application.assistant.ports import VisionLlmPort

INVOICE_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")
MATERIAL_EXTENSIONS = (".jpg", ".jpeg", ".png")

INVOICE_FIELDS = ("total_ttc", "invoice_number", "date", "merchant")
MATERIAL_FIELDS = ("brand", "reference", "name_contains")

#: The plan's own M0 acceptance bar (§7): "total_ttc exact ≥ 19/20, invoice_number ≥
#: 17/20" — expressed as ratios so they scale to however many files are actually present.
TOTAL_TTC_MIN_RATIO = 19 / 20
INVOICE_NUMBER_MIN_RATIO = 17 / 20

#: total_ttc is a float parsed from OCR — exact per the plan, with float-precision slack.
TOTAL_TTC_TOLERANCE = 0.005


@dataclass
class FieldTally:
    correct: int = 0
    total: int = 0
    misses: list[str] = field(default_factory=list)

    def record(self, filename: str, ok: bool) -> None:
        self.total += 1
        if ok:
            self.correct += 1
        else:
            self.misses.append(filename)

    def ratio(self) -> float:
        return self.correct / self.total if self.total else 1.0


def _normalize_invoice_number(value: Optional[str]) -> str:
    return (value or "").strip().lower().replace(" ", "")


def _total_ttc_matches(actual: Optional[float], expected: float) -> bool:
    return actual is not None and abs(actual - expected) <= TOTAL_TTC_TOLERANCE


def _invoice_number_matches(actual: Optional[str], expected: Optional[str]) -> bool:
    return _normalize_invoice_number(actual) == _normalize_invoice_number(expected)


def _date_matches(actual: Optional[str], expected: Optional[str]) -> bool:
    return (actual or None) == (expected or None)


def _contains_ci(actual: Optional[str], expected: Optional[str]) -> bool:
    """Case-insensitive "expected is a substring of actual" — used for merchant name and
    material name_contains, both free-text fields an exact match would be too brittle for."""
    if not actual or not expected:
        return False
    return expected.strip().lower() in actual.strip().lower()


def _text_matches_ci(actual: Optional[str], expected: Optional[str]) -> bool:
    return (actual or "").strip().lower() == (expected or "").strip().lower()


def score_invoice_results(results: dict[str, tuple[Optional[Invoice], dict[str, Any]]]) -> dict[str, FieldTally]:
    """``results``: filename -> (extracted Invoice or None on an extraction failure, gold dict)."""
    tallies = {name: FieldTally() for name in INVOICE_FIELDS}
    for filename, (invoice, gold) in results.items():
        tallies["total_ttc"].record(
            filename, invoice is not None and _total_ttc_matches(invoice.total_ttc, float(gold["total_ttc"]))
        )
        tallies["invoice_number"].record(
            filename,
            invoice is not None and _invoice_number_matches(invoice.invoice_number, gold.get("invoice_number")),
        )
        tallies["date"].record(filename, invoice is not None and _date_matches(invoice.date, gold.get("date")))
        tallies["merchant"].record(
            filename, invoice is not None and _contains_ci(invoice.merchant, gold.get("merchant"))
        )
    return tallies


def score_material_results(
    results: dict[str, tuple[Optional[MaterialIdent], dict[str, Any]]],
) -> dict[str, FieldTally]:
    """``results``: filename -> (identified MaterialIdent or None on a failure, gold dict)."""
    tallies = {name: FieldTally() for name in MATERIAL_FIELDS}
    for filename, (ident, gold) in results.items():
        tallies["brand"].record(filename, ident is not None and _text_matches_ci(ident.brand, gold.get("brand")))
        tallies["reference"].record(
            filename, ident is not None and _text_matches_ci(ident.reference, gold.get("reference"))
        )
        tallies["name_contains"].record(
            filename, ident is not None and _contains_ci(ident.name, gold.get("name_contains"))
        )
    return tallies


def evaluate_acceptance(invoice_tallies: dict[str, FieldTally]) -> bool:
    """The plan's M0 bar — only gates the invoice set (materials have no M0 threshold)."""
    total_ttc = invoice_tallies.get("total_ttc")
    invoice_number = invoice_tallies.get("invoice_number")
    if total_ttc is not None and total_ttc.total > 0 and total_ttc.ratio() < TOTAL_TTC_MIN_RATIO:
        return False
    if invoice_number is not None and invoice_number.total > 0 and invoice_number.ratio() < INVOICE_NUMBER_MIN_RATIO:
        return False
    return True


def _load_gold(directory: Path) -> dict[str, Any]:
    gold_path = directory / "gold.json"
    if not gold_path.exists():
        print(f"error: {gold_path} not found — see eval/README.md for the format.", file=sys.stderr)
        raise SystemExit(2)
    data: dict[str, Any] = json.loads(gold_path.read_text())
    return data


def _iter_files(directory: Path, extensions: tuple[str, ...], limit: Optional[int]) -> list[Path]:
    if not directory.exists():
        return []
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in extensions)
    return files[:limit] if limit else files


def run_invoice_eval(vision: VisionLlmPort, directory: Path, limit: Optional[int]) -> dict[str, FieldTally]:
    gold = _load_gold(directory)
    results: dict[str, tuple[Optional[Invoice], dict[str, Any]]] = {}
    for path in _iter_files(directory, INVOICE_EXTENSIONS, limit):
        gold_entry = gold.get(path.name)
        if gold_entry is None:
            print(f"skip {path.name}: no gold entry", file=sys.stderr)
            continue
        try:
            images = pdf_to_images(path.read_bytes()) if path.suffix.lower() == ".pdf" else [path.read_bytes()]
            invoice: Optional[Invoice] = extract_invoice(vision, images)
        except LlmOutputError as exc:
            print(f"warning: S1 failed for {path.name}: {exc}", file=sys.stderr)
            invoice = None
        results[path.name] = (invoice, gold_entry)
    return score_invoice_results(results)


def run_material_eval(vision: VisionLlmPort, directory: Path, limit: Optional[int]) -> dict[str, FieldTally]:
    gold = _load_gold(directory)
    results: dict[str, tuple[Optional[MaterialIdent], dict[str, Any]]] = {}
    for path in _iter_files(directory, MATERIAL_EXTENSIONS, limit):
        gold_entry = gold.get(path.name)
        if gold_entry is None:
            print(f"skip {path.name}: no gold entry", file=sys.stderr)
            continue
        try:
            ident: Optional[MaterialIdent] = vision.chat_json(
                system=IDENTIFY_SYSTEM_FR,
                user_text=IDENTIFY_USER_TEXT,
                images=[path.read_bytes()],
                model_cls=MaterialIdent,
            )
        except LlmOutputError as exc:
            print(f"warning: A1 failed for {path.name}: {exc}", file=sys.stderr)
            ident = None
        results[path.name] = (ident, gold_entry)
    return score_material_results(results)


def _print_table(title: str, tallies: dict[str, FieldTally]) -> None:
    print(f"\n{title}")
    if not tallies or all(tally.total == 0 for tally in tallies.values()):
        print("  (no files found)")
        return
    for name, tally in tallies.items():
        pct = f"{tally.ratio() * 100:.0f}%"
        print(f"  {name:<16} {tally.correct}/{tally.total} ({pct})")
        for miss in tally.misses:
            print(f"      miss: {miss}")


def _build_vision() -> VisionLlmPort:
    from app.infrastructure.ai.cost import InMemoryCostLedger
    from app.infrastructure.ai.deepseek_client import DeepSeekVisionLlm

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print(
            "error: DEEPSEEK_API_KEY is not set — the eval harness makes real S1/A1 calls, "
            "add it to your environment or .env first.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return DeepSeekVisionLlm(api_key, InMemoryCostLedger())


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoices", type=Path, default=Path("eval/invoices"))
    parser.add_argument("--materials", type=Path, default=Path("eval/materials"))
    parser.add_argument("--limit", type=int, default=None, help="cap the number of files per set")
    args = parser.parse_args(argv)

    try:
        vision = _build_vision()
    except ProviderNotConfiguredError as exc:  # pragma: no cover - _build_vision already exits 2 itself
        print(f"error: {exc}", file=sys.stderr)
        return 2

    invoice_tallies = run_invoice_eval(vision, args.invoices, args.limit) if args.invoices.exists() else {}
    material_tallies = run_material_eval(vision, args.materials, args.limit) if args.materials.exists() else {}

    _print_table("S1 — invoice extraction", invoice_tallies)
    _print_table("A1 — material identification", material_tallies)

    if evaluate_acceptance(invoice_tallies):
        print("\nOK: M0 acceptance met (total_ttc >= 95%, invoice_number >= 85%).")
        return 0
    print("\nFAILED: M0 acceptance not met (total_ttc >= 95%, invoice_number >= 85%).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
