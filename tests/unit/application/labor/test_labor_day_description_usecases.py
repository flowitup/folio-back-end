"""Unit tests for labor day description use-cases.

All external collaborators are replaced with in-memory fakes — no DB, no Flask.

Covers:
- SetLaborDayDescriptionUseCase: create on first call, upsert on repeat same day,
  blank/empty description deletes row and returns None
- ListLaborDayDescriptionsUseCase: returns DTO list sorted by date
- Domain entity: rejects empty description
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import pytest

from app.application.labor.labor_day_description_usecases import (
    LaborDayDescriptionDetail,
    ListLaborDayDescriptionsRequest,
    ListLaborDayDescriptionsUseCase,
    SetLaborDayDescriptionRequest,
    SetLaborDayDescriptionUseCase,
)
from app.application.labor.ports import ILaborDayDescriptionRepository
from app.domain.entities.labor_day_description import LaborDayDescription


# ---------------------------------------------------------------------------
# In-memory repository fake
# ---------------------------------------------------------------------------


class _InMemoryDayDescriptionRepo(ILaborDayDescriptionRepository):
    """Minimal in-memory implementation for unit tests."""

    def __init__(self) -> None:
        # key: (project_id, date) → entity
        self._store: Dict[tuple, LaborDayDescription] = {}

    def find_by_project_and_date(self, project_id: UUID, description_date: date) -> Optional[LaborDayDescription]:
        return self._store.get((project_id, description_date))

    def upsert(self, entity: LaborDayDescription) -> LaborDayDescription:
        key = (entity.project_id, entity.date)
        self._store[key] = entity
        return entity

    def list_by_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
    ) -> List[LaborDayDescription]:
        results = [e for (pid, d), e in self._store.items() if pid == project_id and date_from <= d <= date_to]
        return sorted(results, key=lambda e: e.date)

    def delete_by_date(self, project_id: UUID, description_date: date) -> bool:
        key = (project_id, description_date)
        if key not in self._store:
            return False
        del self._store[key]
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo() -> _InMemoryDayDescriptionRepo:
    return _InMemoryDayDescriptionRepo()


def _set_req(
    project_id: UUID,
    desc_date: date,
    description: str,
    created_by: Optional[UUID] = None,
) -> SetLaborDayDescriptionRequest:
    return SetLaborDayDescriptionRequest(
        project_id=project_id,
        date=desc_date,
        description=description,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Domain entity validation
# ---------------------------------------------------------------------------


class TestLaborDayDescriptionEntity:
    def test_empty_description_raises(self):
        """LaborDayDescription rejects empty description string."""
        with pytest.raises(ValueError, match="must not be empty"):
            LaborDayDescription(
                id=uuid4(),
                project_id=uuid4(),
                date=date(2026, 1, 1),
                description="",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    def test_whitespace_only_description_raises(self):
        """LaborDayDescription rejects whitespace-only description."""
        with pytest.raises(ValueError, match="must not be empty"):
            LaborDayDescription(
                id=uuid4(),
                project_id=uuid4(),
                date=date(2026, 1, 1),
                description="   ",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    def test_valid_description_accepted(self):
        """Non-empty description creates entity without error."""
        entity = LaborDayDescription(
            id=uuid4(),
            project_id=uuid4(),
            date=date(2026, 1, 1),
            description="Foundation work",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert entity.description == "Foundation work"


# ---------------------------------------------------------------------------
# SetLaborDayDescriptionUseCase — create
# ---------------------------------------------------------------------------


class TestSetDescriptionCreate:
    def test_creates_new_entry_on_first_call(self):
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid = uuid4()
        result = uc.execute(_set_req(pid, date(2026, 1, 10), "Rainy day, half crew"))
        assert isinstance(result, LaborDayDescriptionDetail)
        assert result.description == "Rainy day, half crew"
        assert result.date == "2026-01-10"

    def test_strips_whitespace_from_description(self):
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        result = uc.execute(_set_req(uuid4(), date(2026, 1, 10), "  Site delay  "))
        assert result.description == "Site delay"

    def test_created_by_stored(self):
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        user_id = uuid4()
        result = uc.execute(_set_req(uuid4(), date(2026, 1, 10), "Weather hold", created_by=user_id))
        assert result.created_by == str(user_id)

    def test_returns_detail_dto(self):
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        result = uc.execute(_set_req(uuid4(), date(2026, 2, 1), "Full day"))
        assert hasattr(result, "id")
        assert hasattr(result, "project_id")
        assert hasattr(result, "date")
        assert hasattr(result, "description")


# ---------------------------------------------------------------------------
# SetLaborDayDescriptionUseCase — upsert (same day)
# ---------------------------------------------------------------------------


class TestSetDescriptionUpsert:
    def test_second_call_same_day_updates_description(self):
        """Calling set twice with the same (project, date) updates the existing entry."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid = uuid4()
        day = date(2026, 2, 15)

        first = uc.execute(_set_req(pid, day, "Initial description"))
        second = uc.execute(_set_req(pid, day, "Updated description"))

        assert first.id == second.id
        assert second.description == "Updated description"

    def test_only_one_entry_after_two_calls(self):
        """After two calls for the same day, exactly one row exists."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        list_uc = ListLaborDayDescriptionsUseCase(repo)
        pid = uuid4()
        day = date(2026, 3, 1)

        uc.execute(_set_req(pid, day, "First"))
        uc.execute(_set_req(pid, day, "Second"))

        results = list_uc.execute(ListLaborDayDescriptionsRequest(project_id=pid, date_from=day, date_to=day))
        assert len(results) == 1
        assert results[0].description == "Second"

    def test_different_days_create_separate_entries(self):
        """Same project, different dates → two distinct entries."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        list_uc = ListLaborDayDescriptionsUseCase(repo)
        pid = uuid4()

        uc.execute(_set_req(pid, date(2026, 4, 1), "Day one"))
        uc.execute(_set_req(pid, date(2026, 4, 2), "Day two"))

        results = list_uc.execute(
            ListLaborDayDescriptionsRequest(
                project_id=pid,
                date_from=date(2026, 4, 1),
                date_to=date(2026, 4, 30),
            )
        )
        assert len(results) == 2

    def test_different_projects_same_day_are_independent(self):
        """Different projects on the same day are independent."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid_a, pid_b = uuid4(), uuid4()
        day = date(2026, 5, 10)

        uc.execute(_set_req(pid_a, day, "Project A"))
        uc.execute(_set_req(pid_b, day, "Project B"))

        list_uc = ListLaborDayDescriptionsUseCase(repo)
        a_results = list_uc.execute(ListLaborDayDescriptionsRequest(project_id=pid_a, date_from=day, date_to=day))
        b_results = list_uc.execute(ListLaborDayDescriptionsRequest(project_id=pid_b, date_from=day, date_to=day))
        assert len(a_results) == 1
        assert len(b_results) == 1
        assert a_results[0].description == "Project A"
        assert b_results[0].description == "Project B"


# ---------------------------------------------------------------------------
# SetLaborDayDescriptionUseCase — blank clears row
# ---------------------------------------------------------------------------


class TestSetDescriptionBlankClears:
    def test_blank_description_returns_none(self):
        """Empty description string causes use-case to return None."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid = uuid4()
        day = date(2026, 6, 1)
        uc.execute(_set_req(pid, day, "Some description"))

        result = uc.execute(_set_req(pid, day, ""))
        assert result is None

    def test_whitespace_description_returns_none(self):
        """Whitespace-only description returns None."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid = uuid4()
        day = date(2026, 6, 2)
        uc.execute(_set_req(pid, day, "Initial"))

        result = uc.execute(_set_req(pid, day, "   "))
        assert result is None

    def test_blank_description_deletes_row(self):
        """After a blank description call, the row is gone from the store."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        list_uc = ListLaborDayDescriptionsUseCase(repo)
        pid = uuid4()
        day = date(2026, 6, 3)

        uc.execute(_set_req(pid, day, "To be deleted"))
        uc.execute(_set_req(pid, day, ""))

        results = list_uc.execute(ListLaborDayDescriptionsRequest(project_id=pid, date_from=day, date_to=day))
        assert results == []

    def test_blank_on_nonexistent_row_returns_none_without_error(self):
        """Blank description on a day with no existing row returns None gracefully."""
        repo = _make_repo()
        uc = SetLaborDayDescriptionUseCase(repo)
        pid = uuid4()
        day = date(2026, 6, 4)

        result = uc.execute(_set_req(pid, day, ""))
        assert result is None


# ---------------------------------------------------------------------------
# ListLaborDayDescriptionsUseCase
# ---------------------------------------------------------------------------


class TestListLaborDayDescriptions:
    def test_empty_project_returns_empty_list(self):
        repo = _make_repo()
        uc = ListLaborDayDescriptionsUseCase(repo)
        results = uc.execute(
            ListLaborDayDescriptionsRequest(
                project_id=uuid4(),
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            )
        )
        assert results == []

    def test_range_filter_excludes_out_of_range(self):
        """Descriptions outside the requested range are excluded."""
        repo = _make_repo()
        set_uc = SetLaborDayDescriptionUseCase(repo)
        list_uc = ListLaborDayDescriptionsUseCase(repo)
        pid = uuid4()

        set_uc.execute(_set_req(pid, date(2026, 1, 5), "In range"))
        set_uc.execute(_set_req(pid, date(2026, 2, 1), "Out of range"))

        results = list_uc.execute(
            ListLaborDayDescriptionsRequest(
                project_id=pid,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            )
        )
        assert len(results) == 1
        assert results[0].description == "In range"

    def test_list_sorted_by_date_asc(self):
        """Results are sorted by date ascending."""
        repo = _make_repo()
        set_uc = SetLaborDayDescriptionUseCase(repo)
        list_uc = ListLaborDayDescriptionsUseCase(repo)
        pid = uuid4()

        set_uc.execute(_set_req(pid, date(2026, 1, 20), "Last"))
        set_uc.execute(_set_req(pid, date(2026, 1, 5), "First"))
        set_uc.execute(_set_req(pid, date(2026, 1, 12), "Middle"))

        results = list_uc.execute(
            ListLaborDayDescriptionsRequest(
                project_id=pid,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            )
        )
        assert [r.date for r in results] == ["2026-01-05", "2026-01-12", "2026-01-20"]
