"""Person domain entity."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.domain.value_objects.accent_fold import FOLD_FROM, FOLD_TO, fold_text

# The app's one accent fold (searches, dedup): see app.domain.value_objects.accent_fold.
ACCENTED_LETTERS, PLAIN_LETTERS = FOLD_FROM, FOLD_TO


def fold_accents(text: str) -> str:
    """Lowercase ``text`` without its accents: 'Trần Văn Dũng' -> 'tran van dung'."""
    return fold_text((text or "").strip())


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
