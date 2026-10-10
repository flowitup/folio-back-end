"""The billing PDF's logo fetch follows a redirect only to a public address (SSRF guard)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from app.infrastructure.pdf import billing_document_pdf_renderer as pdf_module

LOGO_BYTES = b"\x89PNG\r\n\x1a\nfake-logo"


class _Server:
    """A local HTTP server: /start redirects to ?to=..., /logo.png serves an image; requested paths are recorded."""

    def __init__(self) -> None:
        hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                hits.append(self.path)
                if self.path.startswith("/start?to="):
                    self.send_response(302)
                    self.send_header("Location", self.path.split("=", 1)[1])
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(LOGO_BYTES)

            def log_message(self, *args) -> None:  # silence test output
                pass

        self.hits = hits
        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv(pdf_module._SKIP_LOGO_ENV, raising=False)
    # Talk to the local server directly, never through an environment proxy.
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NO_PROXY", "*")
    srv = _Server()
    yield srv
    srv.close()


def _resolve(public_hosts: set[str]):
    """DNS for _validate_logo_url: the listed hosts look public, any other resolves to loopback."""
    return lambda host: "93.184.216.34" if host in public_hosts else "127.0.0.1"


def test_redirect_to_an_internal_address_is_refused_and_never_requested(server):
    # The stored URL's host passes the check; its redirect lands on loopback.
    start = f"http://127.0.0.1:{server.port}/start?to=http://localhost:{server.port}/ssrf-probe"
    with patch.object(pdf_module.socket, "gethostbyname", side_effect=_resolve({"127.0.0.1"})):
        assert pdf_module._fetch_logo(start) is None
    assert server.hits == [f"/start?to=http://localhost:{server.port}/ssrf-probe"]


def test_redirect_to_the_metadata_endpoint_is_refused(server, caplog):
    start = f"http://127.0.0.1:{server.port}/start?to=http://169.254.169.254/latest/meta-data/"
    with patch.object(
        pdf_module.socket, "gethostbyname", side_effect=lambda h: h.replace("127.0.0.1", "93.184.216.34")
    ):
        with caplog.at_level("WARNING", logger=pdf_module.__name__):
            assert pdf_module._fetch_logo(start) is None
    # Refused by the guard before any connection, not merely failed to connect.
    assert "AWS metadata endpoint" in caplog.text
    assert len(server.hits) == 1


def test_redirect_to_a_public_address_is_still_followed(server):
    start = f"http://127.0.0.1:{server.port}/start?to=/logo.png"
    with patch.object(pdf_module.socket, "gethostbyname", side_effect=_resolve({"127.0.0.1"})):
        buf = pdf_module._fetch_logo(start)
    assert buf is not None and buf.getvalue() == LOGO_BYTES
    assert server.hits[-1] == "/logo.png"


def test_redirect_handler_rejects_a_non_http_target():
    handler = pdf_module._ValidatingRedirectHandler()

    class _Fp:
        closed = False

        def close(self) -> None:
            self.closed = True

    fp = _Fp()
    with pytest.raises(ValueError, match="scheme"):
        handler.redirect_request(None, fp, 302, "Found", {}, "file:///etc/passwd")
    assert fp.closed
