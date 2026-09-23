"""Small per-language rendering/matching helpers for the labor/tasks/admin assistant
features -- computed values (money, dates, shift labels, worker-name matches, a free-text
month), not fixed template copy.

Kept separate from ``reply.py``'s template registry (owned by the core pipeline phase):
these are formatting/matching *functions*, not new user-facing copy. Where a genuinely
new piece of copy is needed (not just a differently-formatted value plugged into an
existing template placeholder), it stays local to the feature module that needs it,
rendered through the same fixed-template-per-language shape ``reply.render`` uses.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from app.application.assistant.aliases import _normalize

# ---------------------------------------------------------------------------
# Money -- vi "1.234,50 €", fr "1 234,50 €", en "€1,234.50". No new
# dependency: plain string formatting, quantized to cents like the rest of the
# money-handling code.
# ---------------------------------------------------------------------------


def format_money(amount: object, lang: str) -> str:
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except (InvalidOperation, ValueError):
        value = Decimal("0")
    value = value.quantize(Decimal("0.01"))
    negative = value < 0
    whole, _, cents = f"{abs(value):.2f}".partition(".")
    grouped = f"{int(whole):,}"
    if lang == "en":
        text = f"\u20ac{grouped}.{cents}"
    elif lang == "vi":
        text = f"{grouped.replace(',', '.')},{cents} \u20ac"
    else:  # fr and any unrecognised language default to fr's own convention
        text = f"{grouped.replace(',', ' ')},{cents} \u20ac"
    return f"-{text}" if negative else text


# ---------------------------------------------------------------------------
# Dates -- vi/fr day-first, en ISO (unambiguous, matches how the rest of the app already
# exchanges dates over the wire).
# ---------------------------------------------------------------------------


def format_date(d: Optional[date], lang: str) -> str:
    if d is None:
        return "-"
    if lang == "en":
        return d.isoformat()
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# Shift type labels -- the roster/attendance templates used to print the raw
# "full"/"half"/"overtime" DB value in every language.
# ---------------------------------------------------------------------------

_SHIFT_LABELS: dict[str, dict[str, str]] = {
    "full": {"vi": "cả ngày", "fr": "journée complète", "en": "full day"},
    "half": {"vi": "nửa ngày", "fr": "demi-journée", "en": "half day"},
    "overtime": {"vi": "tăng ca", "fr": "heures supplémentaires", "en": "overtime"},
}


def shift_type_label(shift_type: Optional[str], lang: str) -> str:
    if not shift_type:
        return "-"
    labels = _SHIFT_LABELS.get(shift_type)
    if labels is None:
        return shift_type
    return labels.get(lang, labels["fr"])


# ---------------------------------------------------------------------------
# Worker-name matching -- accent-insensitive, whole-word/whole-phrase, longest match
# wins.
# ---------------------------------------------------------------------------


def match_names(text: str, names: Iterable[str]) -> list[str]:
    """Every name from ``names`` mentioned in ``text`` as a whole word/phrase,
    accent- and case-insensitive (reuses ``aliases._normalize``'s folding).

    When one matched name is itself contained in a longer matched name (e.g. "An"
    inside "An Nguyễn"), only the longest is kept -- a plain substring/`in` check
    picks up unrelated workers whose name is a prefix or suffix of another's.
    """
    normalized_text = _normalize(text)
    candidates: list[tuple[str, str]] = []
    for name in names:
        stripped = (name or "").strip()
        if not stripped:
            continue
        normalized_name = _normalize(stripped)
        if not normalized_name:
            continue
        if re.search(rf"\b{re.escape(normalized_name)}\b", normalized_text):
            candidates.append((stripped, normalized_name))
    longest_only: list[str] = []
    for name, normalized_name in candidates:
        shadowed_by_a_longer_match = any(
            normalized_name != other_norm and normalized_name in other_norm for _other, other_norm in candidates
        )
        if shadowed_by_a_longer_match:
            continue
        longest_only.append(name)
    return longest_only


# ---------------------------------------------------------------------------
# Month parsing -- "the month this question is about", defaulting to the caller's
# business-calendar month (Europe/Paris) when nothing more specific is said.
# ---------------------------------------------------------------------------

_YM_RE = re.compile(r"\b(\d{4})-(\d{1,2})\b")
#: Matched against the accent-folded text: vi "tháng" folds to "thang".
_MONTH_WORD_RE = re.compile(r"(?:thang|mois|month)\s*[:\-]?\s*(\d{1,2})(?:[/\-](\d{4}))?")

#: Spelled-out month names, vi/fr/en — matched against the accent-folded text (so
#: "février"/"tháng chín" fold to "fevrier"/"thang chin" before this dict is
#: consulted). "salaire de septembre"/"September payroll" named no digit at all, so
#: `_MONTH_WORD_RE` above (numeric only) never matched either; this is the fallback
#: that lets a spelled-out month resolve before giving up and defaulting to the
#: caller's current month. Vietnamese dates almost always use the numeric
#: "tháng 9" form (already handled above) rather than spelling the month out, but the
#: word forms are included for completeness.
_MONTH_NAMES: dict[str, int] = {
    # English
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
    # French
    "janvier": 1,
    "janv": 1,
    "fevrier": 2,
    "fevr": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    # Vietnamese (spelled out, folded)
    "thang mot": 1,
    "thang gieng": 1,
    "thang hai": 2,
    "thang ba": 3,
    "thang tu": 4,
    "thang nam": 5,
    "thang sau": 6,
    "thang bay": 7,
    "thang tam": 8,
    "thang chin": 9,
    "thang muoi": 10,
    "thang muoi mot": 11,
    "thang muoi hai": 12,
}
#: Longest names first, so e.g. "thang muoi mot" (11) is tried before the "thang muoi"
#: (10) prefix it contains.
_MONTH_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(_MONTH_NAMES, key=len, reverse=True)) + r")\b"
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def parse_month(text: str, default: date) -> tuple[int, int]:
    """The ``(year, month)`` a free-text question is about, defaulting to ``default``
    (the caller's current business month) when the text names no month explicitly."""
    match = _YM_RE.search(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return year, month
    normalized = _normalize(text)
    match = _MONTH_WORD_RE.search(normalized)
    if match:
        month = int(match.group(1))
        if 1 <= month <= 12:
            year = int(match.group(2)) if match.group(2) else default.year
            return year, month
    name_match = _MONTH_NAME_RE.search(normalized)
    if name_match:
        month = _MONTH_NAMES[name_match.group(1)]
        year_match = _YEAR_RE.search(normalized)
        year = int(year_match.group(1)) if year_match else default.year
        return year, month
    return default.year, default.month


__all__ = ["format_money", "format_date", "shift_type_label", "match_names", "parse_month"]
