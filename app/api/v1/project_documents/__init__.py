"""Project documents blueprint."""

from flask import Blueprint

project_documents_bp = Blueprint("project_documents", __name__)

from app.api.v1.project_documents import routes  # noqa: E402, F401
