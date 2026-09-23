"""Vi/fr/en alias dictionary for common site tools.

`find_equipment`/`move_equipment` (`equipment.py`) resolve "máy cắt gạch ở đâu?" against
the company's `inventory_items` (name/reference/description) with no LLM call (D6): a
plain ILIKE match only ever catches the exact language a tool happens to be named in, so
this is a small, flat, code-level dictionary of synonym groups — one entry per tool,
listing every language's common name(s). A table would need an admin UI nobody asked
for (KISS, decision D6).
"""

from __future__ import annotations

import re
import unicodedata

#: Each group lists every known name (vi/fr/en, sometimes more than one per language)
#: for the same physical tool. Extend this list as new tools come up; there is no other
#: place aliases live.
ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("máy cắt gạch", "carrelette", "tile cutter"),
    ("máy khoan", "perceuse", "drill"),
    ("máy mài", "meuleuse", "angle grinder", "grinder"),
    ("búa", "marteau", "hammer"),
    ("thang", "échelle", "ladder"),
    ("xe cút kít", "brouette", "wheelbarrow"),
    ("máy phát điện", "groupe électrogène", "generator"),
    ("máy nén khí", "compresseur", "compressor"),
    ("thước thủy", "niveau", "level", "spirit level"),
    ("cưa", "scie", "saw"),
    ("máy hàn", "poste à souder", "welder", "welding machine"),
    ("dây nối dài", "rallonge", "extension cord"),
    ("giàn giáo", "échafaudage", "scaffolding"),
    ("máy trộn bê tông", "bétonnière", "cement mixer", "concrete mixer"),
    ("máy khoan phá bê tông", "marteau-piqueur", "jackhammer", "breaker"),
    ("bay", "truelle", "trowel"),
    ("thước dây", "mètre", "tape measure"),
    ("mũ bảo hộ", "casque", "helmet", "hard hat"),
    ("dây đai an toàn", "harnais", "harness", "safety harness"),
)


def _normalize(text: str) -> str:
    """Lowercase and strip accents (naive Latin folding — good enough for substring matching).

    `đ`/`Đ` has no canonical Unicode decomposition to "d" + a combining stroke (it is an
    atomic Vietnamese letter), so NFKD alone leaves it untouched; folded explicitly so an
    ASCII-typed query ("day dai an toan") still matches a dictionary entry written with
    proper diacritics ("dây đai an toàn").
    """
    ascii_d = text.replace("đ", "d").replace("Đ", "D")
    folded = unicodedata.normalize("NFKD", ascii_d.lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).strip()


#: Vietnamese function/common words that fold (accent-stripped, per ``_normalize``) to
#: the exact same ASCII string as an unrelated alias term -- "của"/"cưa" (of/saw),
#: "bây" (as in "bây giờ", now) matching "bay" (trowel), "tháng" (month) matching
#: "thang" (ladder). Folding necessarily collapses Vietnamese tone marks, so once folded
#: these are indistinguishable from the alias term itself -- word-boundary matching alone
#: cannot tell them apart. So a group term that folds to one of these three words must
#: never match on the folded spelling alone — for a group term that folds to one of these, only the exact accented
#: spelling of the term counts as a match; the collapsed/ASCII spelling does not.
_FOLD_COLLISION_STOPWORDS = frozenset({"cua", "bay", "thang"})


#: A term at least this long tolerates a trailing "s"/"x"/"es" on the haystack side
#: (plural/inflected forms — "échelles", "perceuses", "drills", "ladders") without a
#: dictionary rewrite per language. Shorter terms stay exact: a loose suffix on a
#: 2-3 letter term risks swallowing unrelated words.
_PLURAL_SUFFIX_MIN_LENGTH = 4


def _contains_word(haystack: str, needle: str) -> bool:
    """``needle`` appears in ``haystack`` as a whole word/phrase, not merely as a
    substring straddling other characters (e.g. "bay" must not match inside "ngay").

    A ``needle`` of ``_PLURAL_SUFFIX_MIN_LENGTH`` characters or more also matches its
    own plural/inflected form in ``haystack`` (an optional trailing "s", "x", or "es") —
    without this, a plural query ("où sont les échelles ?", "any drills on site?")
    stopped matching the alias dictionary entirely, because the dictionary only ever
    lists a tool's singular name.
    """
    if not needle:
        return False
    suffix = "(?:s|x|es)?" if len(needle) >= _PLURAL_SUFFIX_MIN_LENGTH else ""
    return re.search(rf"\b{re.escape(needle)}{suffix}\b", haystack) is not None


def _term_matches(term: str, normalized_query: str, raw_query_lower: str) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if normalized_term in _FOLD_COLLISION_STOPWORDS:
        return _contains_word(raw_query_lower, term.lower())
    return _contains_word(normalized_query, normalized_term) or _contains_word(normalized_term, normalized_query)


def expand(query: str) -> list[str]:
    """Every term worth searching for a free-text equipment query.

    Always includes the raw ``query``; adds every term of any alias group where a group
    term appears in the query (or the query appears in a group term -- handles both
    "carrelette" -> "máy cắt gạch" and "máy cắt gạch Bosch" -> "carrelette"),
    matched on whole words/phrases after accent-folding rather than a raw substring check
    (a raw substring match lets an unrelated word that merely *contains* a short alias
    term -- e.g. "ngay" containing "gay" -- pull in a whole tool group by mistake).
    """
    query = query.strip()
    if not query:
        return []
    terms = [query]
    normalized_query = _normalize(query)
    raw_query_lower = query.lower()
    for group in ALIAS_GROUPS:
        matched = any(_term_matches(term, normalized_query, raw_query_lower) for term in group)
        if matched:
            terms.extend(group)
    # Dedupe, case-insensitively, preserving first-seen order.
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in terms:
        key = _normalize(term)
        if key and key not in seen:
            seen.add(key)
            unique_terms.append(term)
    return unique_terms
