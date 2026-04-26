"""Task (planning Kanban) API blueprint."""

from flask import Blueprint

task_bp = Blueprint("tasks", __name__)

from app.api.v1.tasks import task_routes  # noqa: E402, F401
