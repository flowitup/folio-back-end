"""Chat blueprint — team chat channels and messages (enabled by FEATURE_CHAT)."""

from flask import Blueprint

chat_bp = Blueprint("chat", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.chat import routes  # noqa: E402, F401
