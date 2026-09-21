"""Vi/fr/en alias dictionary for common site tools.

`find_equipment`/`move_equipment` (`equipment.py`) resolve "máy cắt gạch ở đâu?" against
the company's `inventory_items` (name/reference/description) with no LLM call (D6): a
plain ILIKE match only ever catches the exact language a tool happens to be named in, so
this is a small, flat, code-level dictionary of synonym groups — one entry per tool,
listing every language's common name(s). A table would need an admin UI nobody asked
for (KISS, decision D6).
"""

from __future__ import annotations

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


def expand(query: str) -> list[str]:
    """Every term worth searching for a free-text equipment query.

    Always includes the raw ``query``; adds every term of any alias group where a group
    term appears in the query or the query appears in a group term (handles both
    "carrelette" -> "máy cắt gạch" and "máy cắt gạch Bosch" -> "carrelette").
    """
    query = query.strip()
    if not query:
        return []
    terms = [query]
    normalized_query = _normalize(query)
    for group in ALIAS_GROUPS:
        matched = any(normalized_query in _normalize(term) or _normalize(term) in normalized_query for term in group)
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
