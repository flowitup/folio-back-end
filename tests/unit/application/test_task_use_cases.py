"""Task create/update: the assignee must be able to read the task's project."""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.application.task import (
    CreateTaskRequest,
    CreateTaskUseCase,
    InvalidAssigneeError,
    UpdateTaskRequest,
    UpdateTaskUseCase,
)
from app.application.task.ports import ITaskRepository
from app.domain.entities.task import Task, TaskStatus

COMPANY = uuid4()
OTHER_COMPANY = uuid4()
PROJECT = uuid4()


class _Repo(ITaskRepository):
    def __init__(self) -> None:
        self.tasks: dict[UUID, Task] = {}

    def create(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def find_by_id(self, task_id: UUID) -> Optional[Task]:
        return self.tasks.get(task_id)

    def list_by_project(self, project_id, status=None):
        return [t for t in self.tasks.values() if t.project_id == project_id]

    def update(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def delete(self, task_id: UUID) -> bool:
        return self.tasks.pop(task_id, None) is not None

    def max_position(self, project_id, status: TaskStatus) -> int:
        return 0


class _Reader:
    """AuthzReaderPort double: users -> (company, role), plus project assignments."""

    def __init__(self, roles: dict, assigned: set) -> None:
        self._roles = roles
        self._assigned = assigned

    def company_role_for(self, user_id, company_id):
        company, role = self._roles.get(user_id, (None, None))
        return role if company == company_id else None

    def is_assigned(self, user_id, project_id):
        return (user_id, project_id) in self._assigned

    def project_company_id(self, project_id):
        return COMPANY if project_id == PROJECT else None

    def project_exists(self, project_id):
        return project_id == PROJECT

    def grants_for(self, user_id, company_id, project_id):
        return []

    def is_platform_ops(self, user_id):
        return False


ADMIN = uuid4()
ASSIGNED_MEMBER = uuid4()
UNASSIGNED_MANAGER = uuid4()
FOREIGN_ADMIN = uuid4()
UNKNOWN = uuid4()


@pytest.fixture
def reader():
    return _Reader(
        roles={
            ADMIN: (COMPANY, "admin"),
            ASSIGNED_MEMBER: (COMPANY, "member"),
            UNASSIGNED_MANAGER: (COMPANY, "manager"),
            FOREIGN_ADMIN: (OTHER_COMPANY, "admin"),
        },
        assigned={(ASSIGNED_MEMBER, PROJECT)},
    )


@pytest.mark.parametrize("assignee", [ADMIN, ASSIGNED_MEMBER])
def test_create_accepts_an_assignee_who_can_read_the_project(reader, assignee):
    task = CreateTaskUseCase(_Repo(), reader).execute(CreateTaskRequest(PROJECT, "Pour slab", assignee_id=assignee))
    assert task.assignee_id == assignee


@pytest.mark.parametrize("assignee", [UNASSIGNED_MANAGER, FOREIGN_ADMIN, UNKNOWN])
def test_create_refuses_an_assignee_who_cannot_read_the_project(reader, assignee):
    repo = _Repo()
    with pytest.raises(InvalidAssigneeError):
        CreateTaskUseCase(repo, reader).execute(CreateTaskRequest(PROJECT, "Pour slab", assignee_id=assignee))
    assert repo.tasks == {}


@pytest.mark.parametrize("assignee", [UNASSIGNED_MANAGER, FOREIGN_ADMIN, UNKNOWN])
def test_update_refuses_an_assignee_who_cannot_read_the_project(reader, assignee):
    repo = _Repo()
    task = CreateTaskUseCase(repo, reader).execute(CreateTaskRequest(PROJECT, "Pour slab"))
    with pytest.raises(InvalidAssigneeError):
        UpdateTaskUseCase(repo, reader).execute(task.id, UpdateTaskRequest(assignee_id=assignee))
    assert repo.tasks[task.id].assignee_id is None


def test_update_accepts_an_assigned_member(reader):
    repo = _Repo()
    task = CreateTaskUseCase(repo, reader).execute(CreateTaskRequest(PROJECT, "Pour slab"))
    updated = UpdateTaskUseCase(repo, reader).execute(task.id, UpdateTaskRequest(assignee_id=ASSIGNED_MEMBER))
    assert updated.assignee_id == ASSIGNED_MEMBER


class _Users:
    """Every id signs in except the deactivated ones — and an unknown id, like the real repository."""

    def __init__(self, known: set, inactive: set) -> None:
        self._known = known
        self._inactive = inactive

    def is_sign_in_allowed(self, user_id):
        return user_id in self._known and user_id not in self._inactive

    def find_by_id(self, user_id):
        return object() if user_id in self._known else None


def test_an_unknown_assignee_is_told_apart_from_a_deactivated_one(reader):
    users = _Users(known={ADMIN, ASSIGNED_MEMBER}, inactive={ASSIGNED_MEMBER})
    usecase = CreateTaskUseCase(_Repo(), reader, user_repo=users)
    with pytest.raises(InvalidAssigneeError, match="must be a member of this project"):
        usecase.execute(CreateTaskRequest(PROJECT, "Pour slab", assignee_id=UNKNOWN))
    with pytest.raises(InvalidAssigneeError, match="deactivated"):
        usecase.execute(CreateTaskRequest(PROJECT, "Pour slab", assignee_id=ASSIGNED_MEMBER))


def test_invalid_assignee_is_a_value_error_so_routes_answer_400():
    assert issubclass(InvalidAssigneeError, ValueError)


def _task_with_everything(repo, reader):
    from datetime import date

    return CreateTaskUseCase(repo, reader).execute(
        CreateTaskRequest(
            PROJECT, "Pour slab", description="Bring the mixer", assignee_id=ASSIGNED_MEMBER, due_date=date(2026, 10, 1)
        )
    )


def test_update_clears_the_fields_sent_as_null(reader):
    repo = _Repo()
    task = _task_with_everything(repo, reader)
    updated = UpdateTaskUseCase(repo, reader).execute(
        task.id, UpdateTaskRequest(cleared=frozenset({"description", "assignee_id", "due_date"}))
    )
    assert (updated.description, updated.assignee_id, updated.due_date) == (None, None, None)
    assert updated.title == "Pour slab"


def test_update_leaves_omitted_fields_alone(reader):
    repo = _Repo()
    task = _task_with_everything(repo, reader)
    updated = UpdateTaskUseCase(repo, reader).execute(task.id, UpdateTaskRequest(title="Pour the slab"))
    assert updated.description == "Bring the mixer"
    assert updated.assignee_id == ASSIGNED_MEMBER
    assert updated.due_date is not None


def test_title_cannot_be_cleared(reader):
    repo = _Repo()
    task = _task_with_everything(repo, reader)
    updated = UpdateTaskUseCase(repo, reader).execute(task.id, UpdateTaskRequest(cleared=frozenset({"title"})))
    assert updated.title == "Pour slab"
