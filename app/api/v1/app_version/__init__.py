"""Public mobile update policy: the oldest app build still allowed to run."""

from flask import Blueprint

app_version_bp = Blueprint("app_version", __name__)

from app.api.v1.app_version import routes  # noqa: E402,F401
