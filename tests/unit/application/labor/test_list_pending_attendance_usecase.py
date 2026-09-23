"""Unit tests for ListPendingAttendanceUseCase's company/project filter forwarding.

The filter itself is applied in SQL by the query port (see
tests/test_labor_repository.py for the real-database proof); this file only pins the
use case's own contract — the filter reaches the port unchanged, and the existing
caller (the notification bell, `user_id` only) keeps working with no filter at all.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from app.application.labor.list_pending_attendance import ListPendingAttendanceUseCase
from app.application.labor.ports import PendingAttendanceItem


class _FakePendingAttendanceQuery:
    def __init__(self, items: List[PendingAttendanceItem]) -> None:
        self._items = items
        self.calls: list[dict] = []

    def list_pending_for_validator(
        self,
        user_id: UUID,
        limit: int = 100,
        *,
        company_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
    ) -> List[PendingAttendanceItem]:
        self.calls.append({"user_id": user_id, "limit": limit, "company_id": company_id, "project_id": project_id})
        return self._items


def test_execute_forwards_no_filter_by_default() -> None:
    """The notification bell's existing call site (`execute(user_id=...)`) must keep
    asking for every project/company the caller may validate — unchanged behaviour."""
    query = _FakePendingAttendanceQuery([])
    use_case = ListPendingAttendanceUseCase(query)
    user_id = uuid4()

    use_case.execute(user_id=user_id)

    assert query.calls == [{"user_id": user_id, "limit": 100, "company_id": None, "project_id": None}]


def test_execute_forwards_the_company_filter_to_the_query_port() -> None:
    query = _FakePendingAttendanceQuery([])
    use_case = ListPendingAttendanceUseCase(query)
    user_id, company_id = uuid4(), uuid4()

    use_case.execute(user_id=user_id, company_id=company_id)

    assert query.calls == [{"user_id": user_id, "limit": 100, "company_id": company_id, "project_id": None}]


def test_execute_forwards_the_project_filter_to_the_query_port() -> None:
    query = _FakePendingAttendanceQuery([])
    use_case = ListPendingAttendanceUseCase(query)
    user_id, project_id = uuid4(), uuid4()

    use_case.execute(user_id=user_id, project_id=project_id)

    assert query.calls == [{"user_id": user_id, "limit": 100, "company_id": None, "project_id": project_id}]
