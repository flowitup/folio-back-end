"""Task (planning Kanban) API routes."""

from __future__ import annotations

from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from pydantic import ValidationError

from app.api.v1.projects.decorators import (
    require_permission,
    require_project_access,
    require_task_access,
)
from app.api.v1.projects.schemas import ErrorResponse
from app.api.v1.tasks import task_bp
from app.api.v1.tasks.schemas import CreateTaskSchema, MoveTaskSchema, UpdateTaskSchema
from app.application.task import (
    CreateTaskRequest,
    TaskNotFoundError,
    UpdateTaskRequest,
)
from app.domain.entities.task import TaskPriority, TaskStatus
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _validation_error(e: ValidationError) -> Tuple[Response, int]:
    return _error_response("ValidationError", str(e), 400)


def _serialize(task) -> dict:
    return {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "priority": task.priority.value,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "position": task.position,
        "labels": list(task.labels),
        "created_by": str(task.created_by) if task.created_by else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Project-scoped: list + create
# ---------------------------------------------------------------------------


@task_bp.route("/projects/<project_id>/tasks", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_tasks(project_id: str):
    status_param = request.args.get("status")
    parsed_status = None
    if status_param:
        try:
            parsed_status = TaskStatus(status_param)
        except ValueError:
            return _error_response("ValidationError", f"Invalid status '{status_param}'", 400)
    tasks = get_container().list_tasks_usecase.execute(UUID(project_id), parsed_status)
    return jsonify({"tasks": [_serialize(t) for t in tasks], "total": len(tasks)})


@task_bp.route("/projects/<project_id>/tasks", methods=["POST"])
@jwt_required()
@require_permission("project:read")  # any member can create tasks
@require_project_access(write=False)
def create_task(project_id: str):
    try:
        data = CreateTaskSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error(e)

    created_by = UUID(get_jwt()["sub"])
    try:
        result = get_container().create_task_usecase.execute(
            CreateTaskRequest(
                project_id=UUID(project_id),
                title=data.title,
                description=data.description,
                status=TaskStatus(data.status),
                priority=TaskPriority(data.priority),
                assignee_id=data.assignee_id,
                due_date=data.due_date,
                labels=data.labels,
                created_by=created_by,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    return jsonify(_serialize(result)), 201


# ---------------------------------------------------------------------------
# Task-scoped: get, update, move, delete
# ---------------------------------------------------------------------------


@task_bp.route("/tasks/<task_id>", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_task_access(write=False)
def get_task(task_id: str):
    try:
        task = get_container().get_task_usecase.execute(UUID(task_id))
    except TaskNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    return jsonify(_serialize(task))


@task_bp.route("/tasks/<task_id>", methods=["PUT"])
@jwt_required()
@require_permission("project:read")
@require_task_access(write=False)  # any project member may edit task content (kept lenient)
def update_task(task_id: str):
    try:
        data = UpdateTaskSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error(e)

    try:
        result = get_container().update_task_usecase.execute(
            UUID(task_id),
            UpdateTaskRequest(
                title=data.title,
                description=data.description,
                priority=TaskPriority(data.priority) if data.priority else None,
                assignee_id=data.assignee_id,
                due_date=data.due_date,
                labels=data.labels,
            ),
        )
    except TaskNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    return jsonify(_serialize(result))


@task_bp.route("/tasks/<task_id>/move", methods=["PATCH"])
@jwt_required()
@require_permission("project:read")
@require_task_access(write=False)
def move_task(task_id: str):
    try:
        data = MoveTaskSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error(e)
    try:
        result = get_container().move_task_usecase.execute(
            UUID(task_id),
            new_status=TaskStatus(data.status),
            before_id=data.before_id,
            after_id=data.after_id,
        )
    except TaskNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    return jsonify(_serialize(result))


@task_bp.route("/tasks/<task_id>", methods=["DELETE"])
@jwt_required()
@require_permission("project:read")
# Destructive operation — restrict to project owner / admin to prevent any
# read-only member from removing other members' tasks.
@require_task_access(write=True)
def delete_task(task_id: str):
    try:
        get_container().delete_task_usecase.execute(UUID(task_id))
    except TaskNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    return "", 204
