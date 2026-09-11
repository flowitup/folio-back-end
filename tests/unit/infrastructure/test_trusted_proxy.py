"""The address every rate limit is keyed on, when the API runs behind a proxy.

Production reaches Flask through cloudflared (mobile, direct API calls) or through the
Next.js server actions (the web app), so the socket peer is the same for everybody. Unless
the forwarded address is honoured, flask-limiter's "5 per minute" on the sign-in routes and
its "100 per minute" default become one bucket for the whole deployment: six people asking
for a code in the same minute lock the sixth out.
"""

from __future__ import annotations

import importlib

import pytest
from flask import Flask
from flask_limiter.util import get_remote_address

from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app
from app.infrastructure.trusted_proxy import apply_trusted_proxy
from config import TestingConfig

CLIENT = "203.0.113.7"
PROXY = "10.1.0.9"


def _app(hops: int) -> Flask:
    app = Flask(__name__)
    app.config["TRUSTED_PROXY_HOPS"] = hops

    @app.route("/who")
    def who() -> str:
        # The exact function flask-limiter keys every limit on.
        return get_remote_address() or ""

    apply_trusted_proxy(app)
    return app


def _seen_by(app: Flask, *, forwarded_for: str | None) -> str:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for is not None else {}
    response = app.test_client().get("/who", headers=headers, environ_base={"REMOTE_ADDR": PROXY})
    return response.get_data(as_text=True)


def test_no_trusted_proxy_keeps_the_socket_peer() -> None:
    """The default deployment is not behind a proxy, so a forged header must change nothing."""
    assert _seen_by(_app(0), forwarded_for=CLIENT) == PROXY


def test_negative_hops_are_treated_as_none() -> None:
    assert _seen_by(_app(-1), forwarded_for=CLIENT) == PROXY


def test_one_trusted_proxy_reads_the_forwarded_client() -> None:
    assert _seen_by(_app(1), forwarded_for=CLIENT) == CLIENT


def test_one_trusted_proxy_reads_the_hop_it_trusts_not_the_client_s_own_claim() -> None:
    """A caller can prepend anything; the proxy appends the address it saw, which wins."""
    assert _seen_by(_app(1), forwarded_for=f"198.51.100.4, {CLIENT}") == CLIENT


def test_missing_header_falls_back_to_the_socket_peer() -> None:
    """Requests that never crossed the proxy — a health probe on the internal network."""
    assert _seen_by(_app(1), forwarded_for=None) == PROXY


class TestWiredIntoTheApp:
    """The helper above is only worth anything if create_app actually calls it."""

    def test_create_app_wraps_the_app_when_a_proxy_is_configured(self) -> None:
        class _BehindOneProxy(TestingConfig):
            TRUSTED_PROXY_HOPS: int = 1

        assert isinstance(create_app(_BehindOneProxy).wsgi_app, ProxyFix)

    def test_create_app_leaves_the_app_alone_by_default(self) -> None:
        assert not isinstance(create_app(TestingConfig).wsgi_app, ProxyFix)

    def test_config_reads_the_documented_environment_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The name appears once in the code and twice in the docs; a typo would be silent."""
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
        import config

        try:
            assert importlib.reload(config).Config.TRUSTED_PROXY_HOPS == 3
        finally:
            monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
            importlib.reload(config)
