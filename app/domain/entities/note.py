"""Note domain entity — models a project-shared note with optional reminder."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

# Allowed lead-time values (in minutes).
# 0    = fire at 09:00 UTC on the due date
# 60   = fire 1 hour before  → 08:00 UTC on the due date
# 1440 = fire 1 day before   → 09:00 UTC the day before due date
VALID_LEAD_TIMES: frozenset[int] = frozenset({0, 60, 1440})

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


def _validate_lead_time(lead_time_minutes: int) -> int:
    """Validate lead_time is in allowed set. Raises InvalidLeadTimeError on failure."""
    # Local import avoids circular dependency (exceptions → note → exceptions).
    from app.application.notes.exceptions import InvalidLeadTimeError

    if lead_time_minutes not in VALID_LEAD_TIMES:
        raise InvalidLeadTimeError(
            f"lead_time_minutes must be one of {sorted(VALID_LEAD_TIMES)}, " f"got {lead_time_minutes}."
        )
    return lead_time_minutes


def _validate_status(status: str) -> Literal["open", "done"]:
    """Validate status. Raises ValueError on failure."""
    if status not in ("open", "done"):
        raise ValueError(f"Note status must be 'open' or 'done', got '{status}'.")
    return status  # type: ignore[return-value]


# ------------------------------------------------------------------
# Entity
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """
    Immutable project-shared note entity.

    State transitions (mark_done / mark_open / with_updates) always return
    new instances — never mutate in place.
    """

    id: UUID
    project_id: UUID
    created_by: UUID
    title: str
    description: str | None
    due_date: date
    lead_time_minutes: int  # ∈ {0, 60, 1440}
    status: Literal["open", "done"]
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
        due_date: date,
        lead_time_minutes: int = 0,
    ) -> Note:
        """
        Create a new open Note with validated fields.

        Raises:
            ValueError: if title or description fail validation.
            InvalidLeadTimeError: if lead_time_minutes ∉ {0, 60, 1440}.
        """
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            created_by=created_by,
            title=_validate_title(title),
            description=_validate_description(description),
            due_date=due_date,
            lead_time_minutes=_validate_lead_time(lead_time_minutes),
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
        due_date: date | None = None,
        lead_time_minutes: int | None = None,
    ) -> Note:
        """
        Return a new Note with the given fields replaced.

        Pass ``description=None`` explicitly to clear the description.
        Omitting ``description`` (or passing the sentinel) leaves it unchanged.

        Raises:
            ValueError: if title or description fail validation.
            InvalidLeadTimeError: if lead_time_minutes ∉ {0, 60, 1440}.
        """
        new_title = _validate_title(title) if title is not None else self.title
        new_desc: str | None
        if isinstance(description, _Unset):
            new_desc = self.description
        else:
            new_desc = _validate_description(description)  # _Unset branch excluded by isinstance guard above
        new_due = due_date if due_date is not None else self.due_date
        new_lead = _validate_lead_time(lead_time_minutes) if lead_time_minutes is not None else self.lead_time_minutes
        return replace(
            self,
            title=new_title,
            description=new_desc,
            due_date=new_due,
            lead_time_minutes=new_lead,
            updated_at=datetime.now(timezone.utc),
        )

    def mark_done(self) -> Note:
        """Return a copy with status='done'."""
        return replace(self, status="done", updated_at=datetime.now(timezone.utc))

    def mark_open(self) -> Note:
        """Return a copy with status='open'."""
        return replace(self, status="open", updated_at=datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def fire_at(due_date: date, lead_time_minutes: int) -> datetime:
        """
        Compute the UTC datetime when a reminder notification should fire.

        Formula: combine(due_date, 09:00 UTC) − lead_time_minutes.

        Examples:
            lead_time=0    → 09:00 UTC on due_date
            lead_time=60   → 08:00 UTC on due_date
            lead_time=1440 → 09:00 UTC the day before due_date
        """
        anchor = datetime.combine(due_date, time(9, 0)).replace(tzinfo=timezone.utc)
        return anchor - timedelta(minutes=lead_time_minutes)
