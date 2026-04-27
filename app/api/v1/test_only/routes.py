"""Test-only blueprint — registered ONLY when app.config['TESTING'] is True.

Security: this blueprint exposes in-memory email state for Playwright e2e tests.
It MUST never be reachable in production. The gate in app/api/v1/__init__.py
enforces this, and test_test_only_blueprint_off_in_prod.py asserts the 404.
"""

from flask import Blueprint, jsonify

test_only_bp = Blueprint("test_only", __name__)


@test_only_bp.route("/last-email", methods=["GET"])
def last_email():
    """Return the last EmailPayload captured by InMemoryEmailAdapter, or 204 if empty.

    Response JSON: {to, subject, body, html_body}
    """
    from wiring import _inmemory_email_adapter

    adapter = _inmemory_email_adapter
    if adapter is None or not adapter.sent:
        return "", 204

    last = adapter.sent[-1]
    return jsonify(
        {
            "to": last.to,
            "subject": last.subject,
            "body": last.body,
            "html_body": last.html_body,
        }
    ), 200
