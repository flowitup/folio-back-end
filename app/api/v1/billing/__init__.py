"""Billing API blueprints.

Three blueprints:
  billing_documents_bp  — /billing-documents (10 endpoints)
  billing_templates_bp  — /billing-document-templates (5 endpoints)
  company_profile_bp    — /company-profile (2 endpoints)

All routes live under the /api/v1 prefix registered in app/__init__.py.
"""

from flask import Blueprint

billing_documents_bp = Blueprint("billing_documents", __name__)
billing_templates_bp = Blueprint("billing_templates", __name__)
company_profile_bp = Blueprint("company_profile", __name__)

# Import route modules to register handlers on the blueprints above.
# noqa comments suppress E402 (module-level import not at top) and F401 (unused import).
from app.api.v1.billing import documents_routes  # noqa: E402, F401
from app.api.v1.billing import templates_routes  # noqa: E402, F401
from app.api.v1.billing import company_profile_routes  # noqa: E402, F401

__all__ = ["billing_documents_bp", "billing_templates_bp", "company_profile_bp"]
