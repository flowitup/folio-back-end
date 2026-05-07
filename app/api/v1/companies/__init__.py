"""Companies API blueprint.

Endpoints (12):
  GET    /companies                          → list_my_companies (or all if ?scope=all + admin)
  POST   /companies                          → create_company (admin)
  GET    /companies/<id>                     → get_company (admin or attached)
  PUT    /companies/<id>                     → update_company (admin)
  DELETE /companies/<id>                     → delete_company (admin)
  POST   /companies/<id>/invite-tokens       → generate_invite_token (admin)
  DELETE /companies/<id>/invite-tokens/active → revoke_invite_token (admin)
  POST   /companies/attach-by-token          → redeem_invite_token (jwt)
  DELETE /companies/<id>/access              → detach_company (jwt + attached)
  DELETE /companies/<id>/access/<user_id>    → boot_attached_user (admin)
  GET    /companies/<id>/attached-users      → list_attached_users (admin)
  PUT    /users/me/primary-company           → set_primary_company (jwt)

All routes live under the /api/v1 prefix registered in app/__init__.py.
"""

from flask import Blueprint

companies_bp = Blueprint("companies", __name__)
users_me_bp = Blueprint("users_me", __name__)

# Import route modules to register handlers on the blueprints above.
# noqa comments suppress E402 (module-level import not at top) and F401 (unused import).
from app.api.v1.companies import routes  # noqa: E402, F401

__all__ = ["companies_bp", "users_me_bp"]
