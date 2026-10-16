"""The one accent- and case-folding rule every search uses.

Users often type without accents (phone keyboards, English or Vietnamese layouts):
'platre' must find 'Plâtre', 'lefevre' 'Lefèvre', 'ong nhua' 'Ống nhựa'. Postgres has no
``unaccent`` extension here, so a column is folded with ``translate()`` over the fixed table
below (Latin-1, Latin Extended-A/B and the Vietnamese Latin Extended Additional block, upper
and lower case, each letter to ONE plain lower-case letter) and the search term is folded in
Python with the very same table, so both sides agree.

Both sides are NFC-normalized first: a name typed or saved in decomposed form ('e' + U+0301,
as macOS and iOS file names often are) folds exactly like the precomposed 'é'.
"""

from __future__ import annotations

import unicodedata

_SPECIAL = {"đ": "d", "Đ": "d", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "ı": "i"}


def _build_table() -> tuple[str, str]:
    src: list[str] = []
    dst: list[str] = []
    for start, end in ((0x00C0, 0x024F), (0x1E00, 0x1EFF)):
        for code in range(start, end + 1):
            char = chr(code)
            if char in _SPECIAL:
                src.append(char)
                dst.append(_SPECIAL[char])
                continue
            base = unicodedata.normalize("NFKD", char)[:1]
            if base != char and base.isascii() and base.isalpha():
                src.append(char)
                dst.append(base.lower())
    return "".join(src), "".join(dst)


FOLD_FROM, FOLD_TO = _build_table()
_FOLD_MAP = str.maketrans(FOLD_FROM, FOLD_TO)


def fold_text(value: str | None) -> str:
    """NFC-normalize, strip Latin accents and lower-case: 'Trần Văn Dũng' -> 'tran van dung'."""
    return unicodedata.normalize("NFC", value or "").translate(_FOLD_MAP).lower()
