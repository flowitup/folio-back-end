"""Invoice API blueprint."""

from flask import Blueprint

invoice_bp = Blueprint("invoices", __name__)

from app.api.v1.invoices import invoice_routes  # noqa: E402, F401
