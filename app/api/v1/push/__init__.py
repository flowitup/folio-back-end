"""Push notification device registration."""

from flask import Blueprint

from app.api._helpers.api_key_request_auth import reject_api_key_mutations

push_bp = Blueprint("push", __name__)
# A device registered through a key would keep receiving the owner's pushes after the key is revoked.
push_bp.before_request(reject_api_key_mutations)

from app.api.v1.push import routes  # noqa: E402,F401
