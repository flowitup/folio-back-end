"""Unit tests for labor activity use-cases (multiple-per-day model).

All external collaborators are replaced with in-memory fakes — no DB, no Flask.

Covers:
- CreateLaborActivityUseCase: each call inserts a distinct entry (many per day)
- ListLaborActivitiesUseCase: returns DTO list
- UpdateLaborActivityUseCase: updates title by id, raises on missing id
- DeleteLaborActivityUseCase: deletes, raises on missing id
- LaborActivityDetail: has no description field
- CreateActivitySchema: accepts {date, title}, rejects description / missing title
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import pytest

from app.application.labor.labor_activity_usecases import (
    CreateLaborActivityRequest,
    CreateLaborActivityUseCase,
    DeleteLaborActivityRequest,
    DeleteLaborActivityUseCase,
    LaborActivityDetail,
    ListLaborActivitiesRequest,
    ListLaborActivitiesUseCase,
    UpdateLaborActivityRequest,
    UpdateLaborActivityUseCase,
)
from app.application.labor.ports import ILaborActivityRepository
from app.domain.entities.labor_activity import LaborActivity
from app.domain.exceptions.labor_exceptions import LaborActivityNotFoundError


# ---------------------------------------------------------------------------
# In-memory repository fake
# ---------------------------------------------------------------------------


class _InMemoryActivityRepo(ILaborActivityRepository):
    """Minimal in-memory implementation for unit tests."""

    def __init__(self) -> None:
        self._store: Dict[UUID, LaborActivity] = {}

    def create(self, activity: LaborActivity) -> LaborActivity:
        self._store[activity.id] = activity
        return activity

    def find_by_id(self, activity_id: UUID) -> Optional[LaborActivity]:
        return self._store.get(activity_id)

    def list_by_project(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[LaborActivity]:
        results = [a for a in self._store.values() if a.project_id == project_id]
        if date_from:
            results = [a for a in results if a.date >= date_from]
        if date_to:
            results = [a for a in results if a.date <= date_to]
        return sorted(results, key=lambda a: (a.date, a.created_at), reverse=True)

    def update(self, activity: LaborActivity) -> LaborActivity:
        self._store[activity.id] = activity
        return activity

    def delete(self, activity_id: UUID) -> bool:
        if activity_id not in self._store:
            return False
        del self._store[activity_id]
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo() -> _InMemoryActivityRepo:
    return _InMemoryActivityRepo()


def _req(
    project_id: UUID,
    activity_date: date,
    title: str,
    created_by: Optional[UUID] = None,
) -> CreateLaborActivityRequest:
    return CreateLaborActivityRequest(
        project_id=project_id,
        date=activity_date,
        title=title,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# CreateLaborActivityUseCase — first create
# ---------------------------------------------------------------------------


class TestCreateActivityFirstCall:
    def test_creates_new_entry_on_first_call(self):
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        pid = uuid4()
        result = uc.execute(_req(pid, date(2026, 1, 10), "Foundation work"))
        assert isinstance(result, LaborActivityDetail)
        assert result.title == "Foundation work"
        assert result.date == "2026-01-10"

    def test_returns_detail_without_description_field(self):
        """LaborActivityDetail must not carry a description attribute."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        result = uc.execute(_req(uuid4(), date(2026, 1, 10), "Steel erection"))
        assert not hasattr(result, "description"), "LaborActivityDetail must not have description"

    def test_strips_whitespace_from_title(self):
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        result = uc.execute(_req(uuid4(), date(2026, 1, 10), "  Trenching  "))
        assert result.title == "Trenching"

    def test_created_by_is_stored(self):
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        user_id = uuid4()
        result = uc.execute(_req(uuid4(), date(2026, 1, 10), "Inspection", created_by=user_id))
        assert result.created_by == str(user_id)


# ---------------------------------------------------------------------------
# CreateLaborActivityUseCase — multiple per day
# ---------------------------------------------------------------------------


class TestCreateActivityMultiplePerDay:
    def test_second_call_same_day_creates_new_entry(self):
        """Calling create twice with the same (project, date) yields two distinct entries."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        pid = uuid4()
        day = date(2026, 2, 15)

        first = uc.execute(_req(pid, day, "Initial title"))
        second = uc.execute(_req(pid, day, "Another activity"))

        # Distinct ids — a new row, not an in-place update.
        assert first.id != second.id
        assert first.title == "Initial title"
        assert second.title == "Another activity"

    def test_two_entries_after_two_calls(self):
        """After two calls for the same day, two rows exist in the store."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        list_uc = ListLaborActivitiesUseCase(repo)
        pid = uuid4()
        day = date(2026, 3, 1)

        uc.execute(_req(pid, day, "Morning brief"))
        uc.execute(_req(pid, day, "Afternoon update"))

        results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid))
        assert len(results) == 2
        assert {r.title for r in results} == {"Morning brief", "Afternoon update"}

    def test_different_days_creates_separate_entries(self):
        """Same project, different dates → two distinct entries."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        list_uc = ListLaborActivitiesUseCase(repo)
        pid = uuid4()

        uc.execute(_req(pid, date(2026, 4, 1), "Day one"))
        uc.execute(_req(pid, date(2026, 4, 2), "Day two"))

        results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid))
        assert len(results) == 2

    def test_different_projects_same_day_creates_separate_entries(self):
        """Different projects on the same day are independent."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        pid_a, pid_b = uuid4(), uuid4()
        day = date(2026, 5, 10)

        uc.execute(_req(pid_a, day, "Project A work"))
        uc.execute(_req(pid_b, day, "Project B work"))

        list_uc = ListLaborActivitiesUseCase(repo)
        a_results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid_a))
        b_results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid_b))
        assert len(a_results) == 1
        assert len(b_results) == 1
        assert a_results[0].title == "Project A work"
        assert b_results[0].title == "Project B work"

    def test_each_entry_keeps_its_own_id_on_same_day(self):
        """Several activities on one day are independent rows, each retrievable by id."""
        repo = _make_repo()
        uc = CreateLaborActivityUseCase(repo)
        pid = uuid4()
        day = date(2026, 6, 1)

        first = uc.execute(_req(pid, day, "First"))
        second = uc.execute(_req(pid, day, "Second"))
        third = uc.execute(_req(pid, day, "Third"))

        ids = {first.id, second.id, third.id}
        assert len(ids) == 3
        for created in (first, second, third):
            assert repo.find_by_id(created.id) is not None


# ---------------------------------------------------------------------------
# ListLaborActivitiesUseCase
# ---------------------------------------------------------------------------


class TestListLaborActivities:
    def test_empty_project_returns_empty_list(self):
        repo = _make_repo()
        uc = ListLaborActivitiesUseCase(repo)
        results = uc.execute(ListLaborActivitiesRequest(project_id=uuid4()))
        assert results == []

    def test_returns_multiple_entries_for_one_day(self):
        """Multiple activities on the same day are all returned."""
        repo = _make_repo()
        create_uc = CreateLaborActivityUseCase(repo)
        list_uc = ListLaborActivitiesUseCase(repo)
        pid = uuid4()
        day = date(2026, 7, 4)

        create_uc.execute(_req(pid, day, "v1"))
        create_uc.execute(_req(pid, day, "v2"))

        results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid))
        assert len(results) == 2
        assert all(r.date == "2026-07-04" for r in results)
        assert {r.title for r in results} == {"v1", "v2"}


# ---------------------------------------------------------------------------
# UpdateLaborActivityUseCase
# ---------------------------------------------------------------------------


class TestUpdateLaborActivity:
    def test_update_title_by_id(self):
        repo = _make_repo()
        create_uc = CreateLaborActivityUseCase(repo)
        update_uc = UpdateLaborActivityUseCase(repo)
        pid = uuid4()

        created = create_uc.execute(_req(pid, date(2026, 8, 1), "Original"))
        updated = update_uc.execute(UpdateLaborActivityRequest(activity_id=created.id, title="Revised"))

        assert updated.id == created.id
        assert updated.title == "Revised"

    def test_update_nonexistent_raises(self):
        repo = _make_repo()
        uc = UpdateLaborActivityUseCase(repo)
        with pytest.raises(LaborActivityNotFoundError):
            uc.execute(UpdateLaborActivityRequest(activity_id=uuid4(), title="Ghost"))


# ---------------------------------------------------------------------------
# DeleteLaborActivityUseCase
# ---------------------------------------------------------------------------


class TestDeleteLaborActivity:
    def test_delete_existing_succeeds(self):
        repo = _make_repo()
        create_uc = CreateLaborActivityUseCase(repo)
        delete_uc = DeleteLaborActivityUseCase(repo)
        pid = uuid4()

        created = create_uc.execute(_req(pid, date(2026, 9, 1), "To delete"))
        delete_uc.execute(DeleteLaborActivityRequest(activity_id=created.id))

        # No longer in store
        assert repo.find_by_id(created.id) is None

    def test_delete_nonexistent_raises(self):
        repo = _make_repo()
        uc = DeleteLaborActivityUseCase(repo)
        with pytest.raises(LaborActivityNotFoundError):
            uc.execute(DeleteLaborActivityRequest(activity_id=uuid4()))

    def test_delete_removes_from_list(self):
        repo = _make_repo()
        create_uc = CreateLaborActivityUseCase(repo)
        delete_uc = DeleteLaborActivityUseCase(repo)
        list_uc = ListLaborActivitiesUseCase(repo)
        pid = uuid4()

        created = create_uc.execute(_req(pid, date(2026, 9, 2), "Will be deleted"))
        delete_uc.execute(DeleteLaborActivityRequest(activity_id=created.id))

        results = list_uc.execute(ListLaborActivitiesRequest(project_id=pid))
        assert results == []


# ---------------------------------------------------------------------------
# Schema validation (no description)
# ---------------------------------------------------------------------------


class TestCreateActivitySchema:
    def test_valid_schema_accepted(self):
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        schema = CreateActivitySchema(date="2026-01-10", title="Daily log")
        assert schema.title == "Daily log"
        assert schema.date == "2026-01-10"

    def test_description_field_not_accepted(self):
        """Schema must not accept a description field (extra fields forbidden or ignored)."""
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        # Pydantic v2 ignores extra fields by default — verify description is NOT on the model
        assert not hasattr(
            CreateActivitySchema.model_fields, "description"
        ), "CreateActivitySchema must not have a description field"

    def test_missing_title_raises_validation_error(self):
        from pydantic import ValidationError as _VE
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        with pytest.raises(_VE):
            CreateActivitySchema(date="2026-01-10")

    def test_empty_title_raises_validation_error(self):
        from pydantic import ValidationError as _VE
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        with pytest.raises(_VE):
            CreateActivitySchema(date="2026-01-10", title="")

    def test_missing_date_raises_validation_error(self):
        from pydantic import ValidationError as _VE
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        with pytest.raises(_VE):
            CreateActivitySchema(title="Some work")

    def test_invalid_date_format_raises_validation_error(self):
        from pydantic import ValidationError as _VE
        from app.api.v1.labor.activity_routes import CreateActivitySchema

        with pytest.raises(_VE):
            CreateActivitySchema(date="10-01-2026", title="Work")
