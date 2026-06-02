"""Note domain entity — models a categorized project journal entry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

# Category scopes a note to the type of journal entry being recorded.
VALID_CATEGORIES: frozenset[str] = frozenset({"inspection", "delivery", "payment", "decision", "call", "general"})

# Status tracks whether a journal note is still actionable.
VALID_STATUSES: frozenset[str] = frozenset({"open", "done"})

_MAX_TITLE_LEN = 200
_MAX_DESC_LEN = 2000


# ------------------------------------------------------------------
# Sentinel for "description not provided" in with_updates
# ------------------------------------------------------------------


class _Unset:
    """Private sentinel type — distinguishes 'not passed' from None."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_UNSET: _Unset = _Unset()


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------


def _validate_title(title: str) -> str:
    """Strip and validate title. Raises ValueError on failure."""
    stripped = title.strip()
    if not stripped:
        raise ValueError("Note title must not be empty.")
    if len(stripped) > _MAX_TITLE_LEN:
        raise ValueError(f"Note title must not exceed {_MAX_TITLE_LEN} characters.")
    return stripped


def _validate_description(description: str | None) -> str | None:
    """Validate description length. Raises ValueError on failure."""
    if description is not None and len(description) > _MAX_DESC_LEN:
        raise ValueError(f"Note description must not exceed {_MAX_DESC_LEN} characters.")
    return description


def _validate_category(category: str) -> str:
    """Validate category is in the allowed set. Raises InvalidCategoryError on failure."""
    # Local import avoids circular dependency (exceptions → note → exceptions).
    from app.application.notes.exceptions import InvalidCategoryError

    if category not in VALID_CATEGORIES:
        raise InvalidCategoryError(f"category must be one of {sorted(VALID_CATEGORIES)}, got '{category}'.")
    return category


def _validate_status(status: str) -> str:
    """Validate status is in the allowed set. Raises ValueError on failure."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}, got '{status}'.")
    return status


# ------------------------------------------------------------------
# Entity
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """
    Immutable project-shared journal note entity.

    State transitions (with_updates) always return new instances — never
    mutate in place.
    """

    id: UUID
    project_id: UUID
    created_by: UUID
    title: str
    description: str | None
    category: str  # ∈ VALID_CATEGORIES
    status: str  # ∈ VALID_STATUSES; default "open"
    created_at: datetime
    updated_at: datetime

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        created_by: UUID,
        title: str,
        description: str | None,
        category: str = "general",
    ) -> Note:
        """
        Create a new journal Note with validated fields.

        New notes always start with status="open".

        Raises:
            ValueError: if title or description fail validation.
            InvalidCategoryError: if category ∉ VALID_CATEGORIES.
        """
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            created_by=created_by,
            title=_validate_title(title),
            description=_validate_description(description),
            category=_validate_category(category),
            status="open",
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # Updates (returns new frozen instance)
    # ------------------------------------------------------------------

    def with_updates(
        self,
        *,
        title: str | None = None,
        description: str | None | _Unset = _UNSET,
        category: str | None = None,
        status: str | None = None,
    ) -> Note:
        """
        Return a new Note with the given fields replaced.

        Pass ``description=None`` explicitly to clear the description.
        Omitting ``description`` (or passing the sentinel) leaves it unchanged.
        Omitting ``status`` (or passing None) leaves it unchanged.

        Raises:
            ValueError: if title, description, or status fail validation.
            InvalidCategoryError: if category ∉ VALID_CATEGORIES.
        """
        new_title = _validate_title(title) if title is not None else self.title
        new_desc: str | None
        if isinstance(description, _Unset):
            new_desc = self.description
        else:
            new_desc = _validate_description(description)  # type: ignore[arg-type]
        new_category = _validate_category(category) if category is not None else self.category
        new_status = _validate_status(status) if status is not None else self.status
        return replace(
            self,
            title=new_title,
            description=new_desc,
            category=new_category,
            status=new_status,
            updated_at=datetime.now(timezone.utc),
        )
