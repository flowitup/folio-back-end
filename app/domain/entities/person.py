"""Person domain entity."""

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


def _accent_table() -> tuple[str, str]:
    """(accented, plain) lowercase letters of the Latin blocks French and Vietnamese use.

    Each accented letter maps to ONE plain letter, so the same pair works in Python
    (``str.translate``) and in SQL (``translate(col, from, to)``) with identical results.
    """
    accented, plain = ["đ"], ["d"]
    for code in list(range(0x00C0, 0x0250)) + list(range(0x1E00, 0x1F00)):
        ch = chr(code)
        if ch != ch.lower():
            continue
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        if len(base) == 1 and base.isascii() and base.isalpha() and base != ch:
            accented.append(ch)
            plain.append(base)
    return "".join(accented), "".join(plain)


ACCENTED_LETTERS, PLAIN_LETTERS = _accent_table()
_FOLD = str.maketrans(ACCENTED_LETTERS, PLAIN_LETTERS)


def fold_accents(text: str) -> str:
    """Lowercase ``text`` without its accents: 'Trần Văn Dũng' -> 'tran van dung'."""
    return (text or "").strip().lower().translate(_FOLD)


@dataclass(slots=True)
class Person:
    """
    Person entity.

    Represents the physical human identity decoupled from any specific
    Project or Company. The same Person can have Worker rows in projects
    belonging to different companies (multi-company support).

    Visibility and scope are enforced at the application layer via Worker
    assignments + Project access permissions — there is no direct company
    FK on this entity.
    """

    id: UUID
    name: str
    normalized_name: str
    created_by_user_id: UUID
    created_at: datetime
    phone: Optional[str] = None
    updated_at: Optional[datetime] = None
    # Phase 2: link to the account this person signed up with, once they do.
    user_id: Optional[UUID] = None
    # Phase 2: E.164 form of `phone` — a matching hint, never unique globally.
    phone_normalized: Optional[str] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Person):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    @staticmethod
    def normalize(name: str) -> str:
        """Return canonical search/dedup form: trimmed + lowercased."""
        return (name or "").strip().lower()
