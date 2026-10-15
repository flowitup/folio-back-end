"""The billing PDF's logo fetch: SSRF guard with a pinned connection, size / time caps, PNG and JPEG only."""

from __future__ import annotations

import ipaddress
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from app.infrastructure.pdf import billing_document_pdf_renderer as pdf_module

PUBLIC_IP = "93.184.216.34"


def _image_bytes(fmt: str = "PNG", size=(40, 20), mode: str = "RGB") -> bytes:
    buf = BytesIO()
    Image.new(mode, size, "red" if mode != "1" else 1).save(buf, format=fmt)
    return buf.getvalue()


LOGO_PNG = _image_bytes("PNG")
LOGO_JPEG = _image_bytes("JPEG")
# A header-only EPS of the kind that loops in Pillow's EPS parser, behind a PNG signature.
EPS_BEHIND_PNG_MAGIC = b"\x89PNG\r\n\x1a\n%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%BeginBinary: -40\n"


class _Server:
    """A local HTTP server standing in for the logo host; records the paths and Host headers it saw."""

    def __init__(self) -> None:
        hits: list[str] = []
        hosts: list[str] = []
        sent = {"endless": 0}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                hits.append(self.path)
                hosts.append(self.headers.get("Host", ""))
                path = self.path
                if path.startswith("/start?to="):
                    self.send_response(302)
                    self.send_header("Location", path.split("=", 1)[1])
                    self.end_headers()
                elif path == "/logo.png":
                    self._ok(LOGO_PNG)
                elif path == "/logo.jpg":
                    self._ok(LOGO_JPEG)
                elif path == "/eps":
                    self._ok(EPS_BEHIND_PNG_MAGIC)
                elif path == "/text":
                    self._ok(b"<html>not an image</html>")
                elif path == "/declared-huge":
                    self.send_response(200)
                    self.send_header("Content-Length", str(pdf_module._MAX_LOGO_BYTES + 1))
                    self.end_headers()
                    self.wfile.write(b"x" * 10)
                elif path == "/endless":
                    self.send_response(200)
                    self.end_headers()
                    chunk = b"\x89PNG\r\n\x1a\n" + b"0" * 65528
                    try:
                        for _ in range(200):  # 12.5 MiB if nobody stops reading
                            self.wfile.write(chunk)
                            sent["endless"] += len(chunk)
                    except OSError:
                        pass
                elif path == "/dribble-body":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(LOGO_PNG[:8])
                    self._dribble(b"0")
                elif path == "/dribble-headers":
                    self.wfile.write(b"HTTP/1.0 200 OK\r\n")
                    self._dribble(b"X-Slow: 1\r\n")
                else:
                    self.send_response(404)
                    self.end_headers()

            def _ok(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _dribble(self, piece: bytes) -> None:
                try:
                    for _ in range(100):
                        self.wfile.write(piece)
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

            def log_message(self, *args) -> None:  # silence test output
                pass

        self.hits, self.hosts, self.sent = hits, hosts, sent
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def net(monkeypatch):
    """A fake network: names resolve through ``dns`` and every connection ends up at the local server."""
    monkeypatch.delenv(pdf_module._SKIP_LOGO_ENV, raising=False)
    server = _Server()
    real_create_connection = pdf_module.socket.create_connection
    real_getaddrinfo = pdf_module.socket.getaddrinfo
    connections: list[tuple[str, int]] = []
    lookups: list[str] = []
    dns: dict[str, list[str] | list[list[str]]] = {"logo.test": [PUBLIC_IP]}

    def getaddrinfo(host, port, *args, **kwargs):
        if host == "127.0.0.1":  # the test's own connection to the local server
            return real_getaddrinfo(host, port, *args, **kwargs)
        lookups.append(host)
        answer = dns[host]
        if answer and isinstance(answer[0], list):  # one answer per lookup: a rebinding host
            answer = answer[min(len(lookups) - 1, len(answer) - 1)]
        return [
            (
                pdf_module.socket.AF_INET6 if ":" in ip else pdf_module.socket.AF_INET,
                pdf_module.socket.SOCK_STREAM,
                6,
                "",
                (ip, port, 0, 0) if ":" in ip else (ip, port),
            )
            for ip in answer
        ]

    def create_connection(address, timeout=None, *args, **kwargs):
        connections.append(address)
        return real_create_connection(("127.0.0.1", server.port), timeout=timeout)

    monkeypatch.setattr(pdf_module.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(pdf_module.socket, "create_connection", create_connection)
    server.connections, server.lookups, server.dns = connections, lookups, dns
    yield server
    server.close()


def _url(path: str) -> str:
    return f"http://logo.test:8080{path}"


# --- happy path -----------------------------------------------------------------------------


def test_png_logo_is_fetched_pinned_and_reencoded(net):
    buf = pdf_module._fetch_logo(_url("/logo.png"))
    assert buf is not None
    img = Image.open(buf)
    assert img.format == "PNG" and img.size == (40, 20)
    # Connected to the vetted address, while the Host header kept the name.
    assert net.connections == [(PUBLIC_IP, 8080)]
    assert net.hosts == ["logo.test:8080"]


def test_jpeg_logo_is_reencoded_as_png(net):
    buf = pdf_module._fetch_logo(_url("/logo.jpg"))
    assert buf is not None and Image.open(buf).format == "PNG"


def test_logo_with_alpha_keeps_its_transparency(net, monkeypatch):
    rgba = _image_bytes("PNG", mode="RGBA")
    monkeypatch.setattr(pdf_module, "_fetch_logo_bytes", lambda url, deadline: rgba)
    assert Image.open(pdf_module._fetch_logo(_url("/x"))).mode == "RGBA"


def test_large_logo_is_downscaled(net, monkeypatch):
    big = _image_bytes("PNG", size=(3000, 1500))
    monkeypatch.setattr(pdf_module, "_fetch_logo_bytes", lambda url, deadline: big)
    assert max(Image.open(pdf_module._fetch_logo(_url("/x"))).size) == pdf_module._LOGO_EMBED_MAX_SIDE


# --- address checks and pinning -----------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "169.254.169.254",
        "100.64.0.1",  # carrier-grade NAT
        "100.100.100.200",
        "224.0.0.1",  # multicast
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "ff02::1",
    ],
)
def test_non_global_address_is_refused_before_any_connection(net, address):
    net.dns["logo.test"] = [address]
    with pytest.raises(ValueError, match="non-public"):
        pdf_module._validate_logo_url(_url("/logo.png"))
    assert pdf_module._fetch_logo(_url("/logo.png")) is None
    assert net.connections == []


def test_one_private_address_among_public_ones_refuses_the_host(net):
    net.dns["logo.test"] = [PUBLIC_IP, "10.0.0.5"]
    assert pdf_module._fetch_logo(_url("/logo.png")) is None
    assert net.connections == []


def test_public_ipv6_address_is_accepted(net):
    net.dns["logo.test"] = ["2606:2800:220:1:248:1893:25c8:1946"]
    assert pdf_module._validate_logo_url(_url("/logo.png")) == ["2606:2800:220:1:248:1893:25c8:1946"]
    assert ipaddress.ip_address(pdf_module._validate_logo_url(_url("/"))[0]).is_global


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://logo.test/a.png", "gopher://logo.test/"])
def test_non_http_scheme_is_refused(net, url):
    with pytest.raises(ValueError, match="scheme"):
        pdf_module._validate_logo_url(url)
    assert pdf_module._fetch_logo(url) is None


def test_dns_rebinding_cannot_redirect_the_connection(net):
    # First lookup (the check) says public, every later one says loopback.
    net.dns["logo.test"] = [[PUBLIC_IP], ["127.0.0.1"]]
    buf = pdf_module._fetch_logo(_url("/logo.png"))
    assert buf is not None
    assert net.lookups == ["logo.test"]  # resolved once; the connection never asked DNS again
    assert net.connections == [(PUBLIC_IP, 8080)]


# --- redirects -------------------------------------------------------------------------------------


def test_redirect_to_an_internal_name_is_refused_and_never_requested(net):
    net.dns["internal.test"] = ["127.0.0.1"]
    assert pdf_module._fetch_logo(_url("/start?to=http://internal.test/probe")) is None
    assert net.hits == ["/start?to=http://internal.test/probe"]
    assert net.connections == [(PUBLIC_IP, 8080)]


def test_redirect_to_the_metadata_endpoint_is_refused(net, caplog):
    net.dns["169.254.169.254"] = ["169.254.169.254"]
    with caplog.at_level("WARNING", logger=pdf_module.__name__):
        assert pdf_module._fetch_logo(_url("/start?to=http://169.254.169.254/latest/meta-data/")) is None
    assert "non-public" in caplog.text
    assert len(net.hits) == 1


def test_redirect_to_a_host_that_rebinds_is_refused(net):
    net.dns["rebind.test"] = ["10.1.2.3"]
    assert pdf_module._fetch_logo(_url("/start?to=http://rebind.test/logo.png")) is None
    assert len(net.hits) == 1


def test_redirect_to_a_public_address_is_followed_and_repinned(net):
    net.dns["cdn.test"] = ["93.184.216.35"]
    buf = pdf_module._fetch_logo(_url("/start?to=http://cdn.test:8081/logo.png"))
    assert buf is not None
    assert net.connections == [(PUBLIC_IP, 8080), ("93.184.216.35", 8081)]
    assert net.hosts[-1] == "cdn.test:8081"


def test_relative_redirect_is_followed(net):
    assert pdf_module._fetch_logo(_url("/start?to=/logo.png")) is not None
    assert net.hits[-1] == "/logo.png"


def test_redirect_to_a_non_http_scheme_is_refused(net):
    assert pdf_module._fetch_logo(_url("/start?to=file:///etc/passwd")) is None
    assert len(net.hits) == 1


def test_redirect_loop_stops_after_a_few_hops(net):
    pdf_module._fetch_logo(_url("/start?to=/start?to=/start"))
    assert len(net.hits) <= pdf_module._MAX_LOGO_REDIRECTS + 1


# --- size, time and format caps -----------------------------------------------------------------


def test_declared_oversized_body_is_refused_without_reading_it(net):
    assert pdf_module._fetch_logo(_url("/declared-huge")) is None


def test_endless_body_is_cut_at_the_cap(net, monkeypatch):
    seen = []
    real = pdf_module._reencode_logo
    monkeypatch.setattr(pdf_module, "_reencode_logo", lambda data: seen.append(len(data)) or real(data))
    assert pdf_module._fetch_logo(_url("/endless")) is None
    assert seen == []  # never even handed to the decoder
    time.sleep(0.2)
    assert net.sent["endless"] < 200 * 65536  # the server was not drained to the end


def test_dribbling_body_hits_the_total_deadline(net, monkeypatch):
    monkeypatch.setattr(pdf_module, "_LOGO_DEADLINE_SECONDS", 0.5)
    started = time.monotonic()
    assert pdf_module._fetch_logo(_url("/dribble-body")) is None
    assert time.monotonic() - started < 2.0


def test_dribbling_headers_hit_the_total_deadline(net, monkeypatch):
    monkeypatch.setattr(pdf_module, "_LOGO_DEADLINE_SECONDS", 0.5)
    started = time.monotonic()
    assert pdf_module._fetch_logo(_url("/dribble-headers")) is None
    assert time.monotonic() - started < 2.0


def test_html_answer_is_not_a_logo(net):
    assert pdf_module._fetch_logo(_url("/text")) is None


def test_eps_behind_a_png_signature_is_not_decoded(net):
    started = time.monotonic()
    assert pdf_module._fetch_logo(_url("/eps")) is None
    assert time.monotonic() - started < 2.0


def test_missing_logo_is_not_an_error(net):
    assert pdf_module._fetch_logo(_url("/nope")) is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"GIF89a" + b"\x00" * 20,
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 1 1\n",
        b"RIFF\x00\x00\x00\x00WEBPVP8 ",
        b"\x89PNG\r\n\x1a\n" + b"junk",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    ],
)
def test_reencode_rejects_everything_but_png_and_jpeg(data):
    try:
        assert pdf_module._reencode_logo(data) is None
    except Exception as exc:  # undecodable PNG-looking bytes; _fetch_logo turns this into "no logo"
        assert type(exc).__name__ in {"UnidentifiedImageError", "OSError", "SyntaxError"}


def test_image_over_the_pixel_cap_is_not_decoded():
    huge = _image_bytes("PNG", size=(5000, 5000), mode="1")
    assert len(huge) < pdf_module._MAX_LOGO_BYTES
    assert pdf_module._reencode_logo(huge) is None


def test_gif_behind_a_png_signature_is_not_decoded():
    gif = BytesIO()
    Image.new("P", (4, 4)).save(gif, format="GIF")
    with pytest.raises(Image.UnidentifiedImageError):
        pdf_module._reencode_logo(b"\x89PNG\r\n\x1a\n" + gif.getvalue())


def test_skip_flag_never_touches_the_network(net, monkeypatch):
    monkeypatch.setenv(pdf_module._SKIP_LOGO_ENV, "1")
    assert pdf_module._fetch_logo(_url("/logo.png")) is None
    assert net.lookups == []


def test_https_connection_connects_to_the_pinned_ip_with_the_original_sni():
    seen: dict = {}

    class _Tls:
        def wrap_socket(self, sock, server_hostname=None, do_handshake_on_connect=True):
            seen["sni"], seen["handshake_on_connect"] = server_hostname, do_handshake_on_connect

            class _Wrapped:
                def do_handshake(self):
                    seen["handshake"] = True

            return _Wrapped()

    deadline = pdf_module._Deadline(5)
    try:
        with patch.object(pdf_module.ssl, "create_default_context", return_value=_Tls()):
            conn = pdf_module._PinnedHTTPSConnection("logo.test", 443, [PUBLIC_IP], deadline)
        with patch.object(pdf_module, "_connect_pinned", return_value=object()) as connect:
            conn.connect()
    finally:
        deadline.cancel()
    assert connect.call_args.args[:2] == ([PUBLIC_IP], 443)
    assert seen == {"sni": "logo.test", "handshake_on_connect": False, "handshake": True}
