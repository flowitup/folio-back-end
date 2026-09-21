"""Integration tests for MoveTaskUseCase against the SQLAlchemy task repository.

Covers the reorder inside one lane: the drop target is the pair of neighbours
(`before_id` above, `after_id` below), and the lane order must follow it even
when the gap between the neighbours has run out.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.application.task.use_cases import CreateTaskRequest, CreateTaskUseCase, MoveTaskUseCase
from app.domain.entities.task import TaskStatus
from app.infrastructure.adapters.sqlalchemy_task import SQLAlchemyTaskRepository


@pytest.fixture
def repo(session):
    return SQLAlchemyTaskRepository(session)


@pytest.fixture
def project_id() -> UUID:
    return uuid4()


def _create(repo, project_id: UUID, title: str):
    return CreateTaskUseCase(repo).execute(
        CreateTaskRequest(project_id=project_id, title=title, status=TaskStatus.TODO)
    )


def _lane_titles(repo, project_id: UUID) -> list[str]:
    return [t.title for t in repo.list_by_project(project_id, TaskStatus.TODO)]


def test_reorder_inside_the_lane_moves_the_card_above_its_neighbour(repo, project_id):
    first = _create(repo, project_id, "first")
    second = _create(repo, project_id, "second")
    assert _lane_titles(repo, project_id) == ["first", "second"]

    moved = MoveTaskUseCase(repo).execute(second.id, new_status=TaskStatus.TODO, after_id=first.id)

    assert moved.position != second.position
    assert moved.position < repo.find_by_id(first.id).position
    assert _lane_titles(repo, project_id) == ["second", "first"]


def test_repeated_move_to_top_keeps_reordering(repo, project_id):
    """Second move to the top has no integer room left — the lane is renumbered."""
    first = _create(repo, project_id, "first")
    second = _create(repo, project_id, "second")
    usecase = MoveTaskUseCase(repo)

    usecase.execute(second.id, new_status=TaskStatus.TODO, after_id=first.id)
    usecase.execute(first.id, new_status=TaskStatus.TODO, after_id=second.id)

    assert _lane_titles(repo, project_id) == ["first", "second"]
    positions = [t.position for t in repo.list_by_project(project_id, TaskStatus.TODO)]
    assert len(set(positions)) == len(positions)


def test_drop_between_two_neighbours_lands_between_them(repo, project_id):
    first = _create(repo, project_id, "first")
    second = _create(repo, project_id, "second")
    third = _create(repo, project_id, "third")

    MoveTaskUseCase(repo).execute(
        third.id,
        new_status=TaskStatus.TODO,
        before_id=first.id,
        after_id=second.id,
    )

    assert _lane_titles(repo, project_id) == ["first", "third", "second"]


def test_move_to_another_lane_without_neighbours_appends(repo, project_id):
    _create(repo, project_id, "first")
    second = _create(repo, project_id, "second")

    moved = MoveTaskUseCase(repo).execute(second.id, new_status=TaskStatus.DONE)

    assert moved.status is TaskStatus.DONE
    assert _lane_titles(repo, project_id) == ["first"]
    assert [t.title for t in repo.list_by_project(project_id, TaskStatus.DONE)] == ["second"]
