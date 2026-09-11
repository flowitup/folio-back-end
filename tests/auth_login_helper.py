"""Sign a test user in without a real HTTP round-trip.

Phone + SMS code is the only real sign-in path now (see
app/application/usecases/otp_login.py); there is no password left to check.
Tests that only need SOME valid session for a known user — rather than to
exercise the sign-in HTTP path itself — mint the same tokens a successful
sign-in would produce, directly through the app's token issuer. Tests that
specifically exercise sign-in (e.g. tests/api/test_otp_login_endpoints.py)
still drive the real `/auth/otp/request` + `/auth/otp/verify` endpoints and
extract the code from the recording SMS sender; this helper is not a
replacement for that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask.testing import FlaskClient


def mint_tokens(client: "FlaskClient", email: str) -> dict:
    """Return {"access_token", "refresh_token"} for the user with this email.

    Raises AssertionError if no such user exists — the equivalent of a login
    failure under the old password-based helper this replaces.
    """
    app = client.application
    with app.app_context():
        from wiring import get_container

        container = get_container()
        user = container.user_repository.find_by_email(email)
        assert user is not None, f"No user with email {email!r} to mint a token for"
        return {
            "access_token": container.token_issuer.create_access_token(user.id),
            "refresh_token": container.token_issuer.create_refresh_token(user.id),
        }


def mint_access_token(client: "FlaskClient", email: str) -> str:
    """Return just the access token — the common case for `Authorization: Bearer` headers."""
    return mint_tokens(client, email)["access_token"]
