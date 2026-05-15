"""Payment methods API blueprint.

Endpoints nested under /companies/<company_id>/payment-methods.
"""

from flask import Blueprint

payment_methods_bp = Blueprint("payment_methods", __name__)

from app.api.v1.payment_methods import routes  # noqa: E402, F401
