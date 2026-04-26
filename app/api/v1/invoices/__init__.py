"""Invoice API blueprint."""

from flask import Blueprint

invoice_bp = Blueprint("invoices", __name__)

from app.api.v1.invoices import invoice_routes  # noqa: E402, F401
from app.api.v1.invoices import attachment_routes  # noqa: E402, F401
