"""ReportLab PDF renderer for BillingDocument entities.

Implements BillingDocumentPdfRendererPort from app.application.billing.ports.

Layout (A4 portrait, 15 mm margins):
  1. Issuer header band  — optional logo, legal_name, address, SIRET, TVA, IBAN
  2. Document title row  — "DEVIS" or "FACTURE" + document_number (right-aligned)
  3. Recipient block (left) + Meta block (right), then "Objet :" (linked project)
  4. Items table         — Description / Qty / Unit price HT / VAT % / Total HT
  5. Totals block        — Total HT, TVA per rate, Total TTC
  6. Notes section       — if present
  7. Terms section       — if present
  8. Signature block     — left placeholder + right signature text
  9. Footer              — "Généré par Folio · Page X / Y" centered

Fonts: DejaVu Sans + DejaVu Sans Bold from app/domain/labor/export/fonts/.
       Reuses the registration guard from pdf_builder — safe to import both.
       Chinese/Japanese/Korean characters (absent from DejaVu) fall back to
       Adobe's standard CJK fonts, which PDF viewers supply (nothing embedded).
Currency: format_eur_fr() from app/domain/labor/export/format.py.
Logo fetch: pinned to a vetted public IP, 1 MiB / 5 s caps, PNG and JPEG only,
            fail-open (logo skipped on any error).
            Skipped entirely when running under test flag FOLIO_SKIP_LOGO_FETCH=1
            or when issuer_logo_url is None/empty.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import logging
import os
import socket
import ssl
import threading
import time
import urllib.parse
import warnings
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

from app.domain.billing.sections import section_headings
from app.domain.billing.document import BillingDocument
from app.domain.billing.document_wording import intro_sentence
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import BillingDocumentRenderError
from app.domain.entities.project import Project
from app.domain.labor.export.format import format_decimal_fr, format_eur_fr, format_unit_price_eur_fr


def _fmt_pct(d) -> str:
    """Format a Decimal percentage the French way: 10 → "10", 5.5 → "5,5"."""
    return format_decimal_fr(d)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font registration — idempotent module-level guard
# ---------------------------------------------------------------------------
# Module-level code is serialised by the Python import system, so registering
# here eliminates the lazy-flag race condition entirely.
# pdfmetrics.registerFont raises KeyError if the name is already registered;
# we catch that (and any other error) so the import never hard-crashes.

_FONTS_DIR = Path(__file__).parent.parent.parent / "domain" / "labor" / "export" / "fonts"

_FONTS_REGISTERED = False

# Fallback fonts for characters DejaVu Sans has no glyph for. These are Adobe's
# standard CJK fonts: viewers supply them, so the PDF keeps the text without
# embedding a large font file.
_CJK_FONT = "STSong-Light"  # Chinese (simplified and traditional) and Japanese
_HANGUL_FONT = "HYGothic-Medium"  # Korean


def _register_fonts_once() -> None:
    """Register DejaVu (and the CJK fallback) fonts exactly once; subsequent calls are no-ops."""
    global _FONTS_REGISTERED  # noqa: PLW0603
    if _FONTS_REGISTERED:
        return
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", str(_FONTS_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(
            "DejaVu",
            normal="DejaVu",
            bold="DejaVu-Bold",
            italic="DejaVu",
            boldItalic="DejaVu-Bold",
        )
        _FONTS_REGISTERED = True
    except Exception as err:  # noqa: BLE001
        warnings.warn(
            f"billing_pdf_renderer: DejaVu font registration failed ({err}). "
            "PDF generation will fail if fonts are unavailable.",
            stacklevel=2,
        )
    for cid_font in (_CJK_FONT, _HANGUL_FONT):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(cid_font))
        except Exception as err:  # noqa: BLE001
            warnings.warn(f"billing_pdf_renderer: {cid_font} registration failed ({err}).", stacklevel=2)


_register_fonts_once()


def _dejavu_codepoints() -> frozenset:
    try:
        return frozenset(pdfmetrics.getFont("DejaVu").face.charToGlyph)
    except Exception:  # noqa: BLE001 — fonts missing: never substitute
        return frozenset()


_DEJAVU_CODEPOINTS = _dejavu_codepoints()


def _fallback_font(ch: str) -> Optional[str]:
    """The CJK font to draw *ch* with, or None when DejaVu has it (or nothing better exists)."""
    cp = ord(ch)
    if cp in _DEJAVU_CODEPOINTS or cp < 0x2E80:
        return None
    if 0x3130 <= cp <= 0x318F or 0xAC00 <= cp <= 0xD7AF:  # Hangul
        return _HANGUL_FONT
    if cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFFEF or 0x20000 <= cp <= 0x2FA1F:
        return _CJK_FONT
    return None


def _pdf_text(text: str) -> str:
    """Escape *text* for a Paragraph, wrapping runs of CJK characters in their fallback font.

    Without this, DejaVu draws Chinese/Japanese/Korean characters as empty boxes.
    """
    parts: list[str] = []
    run: list[str] = []
    run_font: Optional[str] = None

    def flush() -> None:
        if run:
            escaped = _xml_escape("".join(run))
            parts.append(f'<font name="{run_font}">{escaped}</font>' if run_font else escaped)
            run.clear()

    for ch in text:
        font = _fallback_font(ch)
        if font != run_font:
            flush()
            run_font = font
        run.append(ch)
    flush()
    return "".join(parts)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------


def _styles() -> dict:
    title = ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=20, leading=24)
    # Issuer-name title: cyan blue matching the source ANN ECO CONSTRUCTION header.
    issuer_title = ParagraphStyle(
        "issuer_title",
        fontName="DejaVu-Bold",
        fontSize=14,
        leading=18,
        alignment=1,
        textColor=colors.HexColor("#00A0DF"),
    )
    h2 = ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=11, leading=14, spaceAfter=2)
    h3 = ParagraphStyle("h3", fontName="DejaVu-Bold", fontSize=9, leading=12, spaceAfter=1)
    body = ParagraphStyle("body", fontName="DejaVu", fontSize=9, leading=12)
    body_small = ParagraphStyle("body_small", fontName="DejaVu", fontSize=8, leading=10)
    # Figures: Paragraph alignment wins over the table's ALIGN, so numbers need their own styles.
    body_small_center = ParagraphStyle("body_small_center", parent=body_small, alignment=1)
    body_small_right = ParagraphStyle("body_small_right", parent=body_small, alignment=2)
    body_grey = ParagraphStyle("body_grey", fontName="DejaVu", fontSize=8, leading=10, textColor=colors.grey)
    right = ParagraphStyle("right", fontName="DejaVu", fontSize=9, leading=12, alignment=2)
    right_bold = ParagraphStyle("right_bold", fontName="DejaVu-Bold", fontSize=10, leading=13, alignment=2)
    footer_style = ParagraphStyle("footer", fontName="DejaVu", fontSize=8, leading=10, alignment=1)
    label = ParagraphStyle("label", fontName="DejaVu-Bold", fontSize=8, leading=10, textColor=colors.grey)
    # Items header on orange background needs black text (not grey) for contrast.
    label_white = ParagraphStyle(
        "label_white",
        fontName="DejaVu-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.black,
        alignment=1,
    )
    section_title = ParagraphStyle("section_title", fontName="DejaVu-Bold", fontSize=9, leading=12, spaceAfter=2)
    return {
        "title": title,
        "issuer_title": issuer_title,
        "h2": h2,
        "h3": h3,
        "body": body,
        "body_small": body_small,
        "body_small_center": body_small_center,
        "body_small_right": body_small_right,
        "body_grey": body_grey,
        "right": right,
        "right_bold": right_bold,
        "footer": footer_style,
        "label": label,
        "label_white": label_white,
        "section_title": section_title,
    }


# ---------------------------------------------------------------------------
# Logo fetch — fail-open, pinned to the vetted IP, size / time / format bounded
# ---------------------------------------------------------------------------

_SKIP_LOGO_ENV = "FOLIO_SKIP_LOGO_FETCH"
_MAX_LOGO_REDIRECTS = 3
_MAX_LOGO_BYTES = 1024 * 1024  # a logo is a few tens of KB; the cap also bounds the memory a hostile host can cost
_LOGO_DEADLINE_SECONDS = 5.0  # whole fetch (connect, TLS, headers, body), not one socket operation
_MAX_LOGO_PIXELS = 4096 * 4096  # checked from the header, before any pixel is decoded
_LOGO_EMBED_MAX_SIDE = 1200  # the logo prints at 50 x 25 mm: more pixels only bloat the PDF
_LOGO_FORMATS = ("PNG", "JPEG")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global alone still lets IPv4 multicast through, hence the explicit extra checks.
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local)


def _validate_logo_url(url: str) -> list[str]:
    """Resolve *url*'s host once and return the vetted addresses to connect to.

    Raises ValueError if:
    - Scheme is not http or https.
    - The host does not resolve, or ANY resolved address (IPv4 or IPv6) is not
      globally routable: private, loopback, link-local (169.254.169.254, the
      cloud metadata endpoint, included), CGNAT 100.64.0.0/10, multicast, reserved.

    The caller connects to the returned addresses, never to the name again, so a
    DNS answer that changes between this check and the connection cannot matter.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Logo URL scheme must be http or https, got: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise ValueError("Logo URL missing host")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise ValueError(f"Logo URL host could not be resolved: {exc}") from exc

    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError(f"Logo URL host resolved to an unreadable address: {exc}") from exc
        if not _is_public_address(ip):
            raise ValueError(f"Logo URL resolves to a non-public address — blocked ({address})")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("Logo URL host did not resolve")
    return addresses


class _Deadline:
    """One wall-clock budget for the whole fetch.

    socket timeouts apply to each send / recv, so a host dribbling one byte a second
    never trips them; the watchdog shuts the socket down when the budget is spent,
    which wakes any blocked read with an error.
    """

    def __init__(self, seconds: float) -> None:
        self.expires = time.monotonic() + seconds
        self.conn: Optional[_PinnedHTTPConnection | _PinnedHTTPSConnection] = None
        self._timer = threading.Timer(seconds, self._abort)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self) -> float:
        left = self.expires - time.monotonic()
        if left <= 0:
            raise TimeoutError("logo fetch deadline exceeded")
        return left

    def _abort(self) -> None:
        sock = getattr(self.conn, "live_sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def cancel(self) -> None:
        self._timer.cancel()


def _connect_pinned(addresses: list[str], port: int, deadline: _Deadline) -> socket.socket:
    last_error: Optional[OSError] = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout=deadline.remaining())
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("no address to connect to")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to an already vetted IP while the Host header keeps the original name."""

    def __init__(self, host: str, port: int, addresses: list[str], deadline: _Deadline) -> None:
        super().__init__(host, port, timeout=deadline.remaining())
        self._addresses = addresses
        self._deadline = deadline
        self.live_sock: Optional[socket.socket] = None  # http.client drops .sock once a response is "will_close"

    def connect(self) -> None:
        self.sock = self.live_sock = _connect_pinned(self._addresses, self.port, self._deadline)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """As _PinnedHTTPConnection, with TLS: SNI and certificate checks use the original host name."""

    def __init__(self, host: str, port: int, addresses: list[str], deadline: _Deadline) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(host, port, timeout=deadline.remaining(), context=self._tls)
        self._addresses = addresses
        self._deadline = deadline
        self.live_sock: Optional[socket.socket] = None

    def connect(self) -> None:
        raw = _connect_pinned(self._addresses, self.port, self._deadline)
        # Published on self.sock before the handshake so the watchdog can also cut a stalled handshake.
        tls = self._tls.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
        self.sock = self.live_sock = tls
        tls.do_handshake()


def _fetch_logo_bytes(url: str, deadline: _Deadline) -> Optional[bytes]:
    """GET *url* and return the body, following up to _MAX_LOGO_REDIRECTS hops, each vetted and pinned the same way.

    Returns None for a non-200 answer or a body over _MAX_LOGO_BYTES (declared or actual).
    Raises ValueError when a URL is refused, OSError / HTTPException on network failures.
    """
    for _hop in range(_MAX_LOGO_REDIRECTS + 1):
        addresses = _validate_logo_url(url)
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        https = parsed.scheme == "https"
        port = parsed.port or (443 if https else 80)
        conn: _PinnedHTTPConnection | _PinnedHTTPSConnection = (
            _PinnedHTTPSConnection(host, port, addresses, deadline)
            if https
            else _PinnedHTTPConnection(host, port, addresses, deadline)
        )
        deadline.conn = conn
        try:
            target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            conn.request(
                "GET",
                target,
                headers={
                    "User-Agent": "Folio-PDF/1.0",
                    "Accept": "image/png, image/jpeg",
                    "Accept-Encoding": "identity",
                },
            )
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    return None
                url = urllib.parse.urljoin(url, location)
                continue
            if resp.status != 200:
                return None
            declared = resp.getheader("Content-Length", "")
            if declared.isdigit() and int(declared) > _MAX_LOGO_BYTES:
                return None
            chunks: list[bytes] = []
            total = 0
            while not resp.isclosed():  # http.client closes the socket as soon as a sized body is complete
                if conn.live_sock is not None:
                    conn.live_sock.settimeout(deadline.remaining())
                chunk = resp.read1(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_LOGO_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            conn.close()
    raise ValueError("Logo URL redirected too many times")


def _reencode_logo(data: bytes) -> Optional[BytesIO]:
    """PNG or JPEG bytes -> a fresh PNG, or None for anything else.

    The magic bytes pick the format before Pillow sees the data, and Pillow is only
    allowed the two decoders (it would otherwise sniff EPS, TIFF, JPEG 2000...).
    ReportLab then only ever receives bytes this function wrote.
    """
    if not (data.startswith(_PNG_MAGIC) or data.startswith(_JPEG_MAGIC)):
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("error", PILImage.DecompressionBombWarning)
        with PILImage.open(BytesIO(data), formats=_LOGO_FORMATS) as img:
            width, height = img.size
            if width < 1 or height < 1 or width * height > _MAX_LOGO_PIXELS:
                return None
            img.load()
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            out = img.convert("RGBA" if has_alpha else "RGB")
    out.thumbnail((_LOGO_EMBED_MAX_SIDE, _LOGO_EMBED_MAX_SIDE))
    buf = BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _fetch_logo(url: Optional[str]) -> Optional[BytesIO]:
    """Fetch an image from *url* and return it, re-encoded as PNG, in a BytesIO buffer.

    Returns None on any error (network failure, timeout, oversized body, not a
    PNG / JPEG, empty URL, env skip flag, SSRF validation failure). Never raises.

    Security: every resolved address of the host (and of every redirect hop) must be
    globally routable, and the connection goes to that vetted address (Host header
    and TLS SNI keep the name); the body is capped at _MAX_LOGO_BYTES and the whole
    fetch at _LOGO_DEADLINE_SECONDS; only PNG / JPEG decoded by Pillow, then
    re-encoded, reach ReportLab.
    """
    if not url:
        return None
    if os.environ.get(_SKIP_LOGO_ENV, "").strip() == "1":
        return None
    deadline = _Deadline(_LOGO_DEADLINE_SECONDS)
    try:
        data = _fetch_logo_bytes(url, deadline)
        if not data:
            return None
        return _reencode_logo(data)
    except ValueError as exc:
        # Log a fingerprint hash only — never log the user-supplied URL to
        # avoid leaking SSRF probe targets in application logs (M10).
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        logger.warning("billing_pdf: logo fetch rejected (url_hash=%s): %s", url_hash, exc)
        return None
    except Exception as err:  # noqa: BLE001
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        logger.debug("billing_pdf: logo fetch failed (url_hash=%s): %s", url_hash, type(err).__name__)
        return None
    finally:
        deadline.cancel()


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

_MARGIN = 15 * mm
_BOTTOM_MARGIN = 22 * mm  # room for footer
# Height flowables get on a page: A4 minus the margins and the frame's 6 pt top/bottom paddings.
_FRAME_HEIGHT = A4[1] - _MARGIN - _BOTTOM_MARGIN - 2 * 6


class _PageTable(Table):
    """Table whose rows taller than a page can always split (see _split_rows_taller_than_a_page)."""

    def split(self, availWidth, availHeight):  # type: ignore[no-untyped-def]  # noqa: N803 — ReportLab API
        parts = super().split(availWidth, availHeight)
        if parts or not self.splitInRow or availHeight < _FRAME_HEIGHT - 1:
            return parts
        # A whole empty page and still no split: the minimum refused a row barely taller than
        # the page, whose remainder would be under it. A thin remainder beats a failed render.
        min_split = self.splitInRow
        self.splitInRow = 0.01
        try:
            parts = super().split(availWidth, availHeight)
        finally:
            self.splitInRow = min_split
        for part in parts:
            part.splitInRow = min_split
        return parts


def _split_rows_taller_than_a_page(table: Table, width: float) -> Table:
    """Let a row taller than a whole page continue on the next page.

    ReportLab never splits inside a row by default, so such a row (a long line
    description, address or signature text) made the build raise LayoutError.
    Rows that fit on a page keep moving to the next page whole.
    """
    table.wrap(width, _FRAME_HEIGHT)
    heights = table._rowHeights  # computed by wrap()
    if max(heights, default=0) > _FRAME_HEIGHT - sum(heights[: table.repeatRows]):
        # Split inside rows first: splitting between rows first would put the repeated
        # header row a second time on the same page, right above the tall row.
        table.splitByRow = 0
        table.splitInRow = 3 * mm  # never leave a sliver of a row at the bottom of a page
        # ReportLab pads each half of a split MIDDLE-aligned cell to re-centre it, so a second
        # tall row (or a third page of one) grows past the page again: top-align split rows.
        table.setStyle(TableStyle([("VALIGN", (0, table.repeatRows), (-1, -1), "TOP")]))
    return table


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_issuer_header(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Issuer header band: optional logo left, company info right."""
    elements = []

    logo_buf = _fetch_logo(doc.issuer_logo_url)

    # Issuer legal name in cyan, bold, sz14, centered — matches source FF00A0DF.
    info_lines = [Paragraph(_pdf_text(doc.issuer_legal_name), styles["issuer_title"])]
    if doc.issuer_address:
        for line in doc.issuer_address.splitlines():
            info_lines.append(Paragraph(_pdf_text(line), styles["body_small"]))
    if doc.issuer_siret:
        info_lines.append(Paragraph(f"SIRET : {_pdf_text(doc.issuer_siret)}", styles["body_small"]))
    if doc.issuer_tva_number:
        info_lines.append(Paragraph(f"TVA : {_pdf_text(doc.issuer_tva_number)}", styles["body_small"]))
    # IBAN / BIC moved to a dedicated "Coordonnées bancaires" block at the
    # bottom of the doc, matching the source-PDF layout for a familiar visual.

    if logo_buf:
        logo_max_h = 25 * mm
        logo_max_w = 50 * mm
        try:
            logo_img = Image(logo_buf, width=logo_max_w, height=logo_max_h, kind="bound")
            logo_cell = logo_img
        except Exception:  # noqa: BLE001
            logo_cell = Paragraph("", styles["body"])
        header_data = [[logo_cell, info_lines]]
        col_widths = [logo_max_w + 4 * mm, usable_width - logo_max_w - 4 * mm]
    else:
        header_data = [["", info_lines]]
        col_widths = [0, usable_width]

    header_table = _PageTable(header_data, colWidths=col_widths)
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(_split_rows_taller_than_a_page(header_table, usable_width))
    return elements


def _build_title_row(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Document title band: kind label left, number right."""
    kind_label = "DEVIS" if doc.kind == BillingDocumentKind.DEVIS else "FACTURE"
    title_data = [
        [
            Paragraph(kind_label, styles["title"]),
            Paragraph(_pdf_text(doc.document_number), styles["right_bold"]),
        ]
    ]
    t = Table(title_data, colWidths=[usable_width * 0.6, usable_width * 0.4])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [t]


def _build_recipient_meta(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Two-column block: recipient left, meta (dates/terms) right."""
    # Recipient column
    recip_lines = [Paragraph(_pdf_text(doc.recipient_name), styles["h3"])]
    if doc.recipient_address:
        for line in doc.recipient_address.splitlines():
            recip_lines.append(Paragraph(_pdf_text(line), styles["body_small"]))
    if doc.recipient_email:
        recip_lines.append(Paragraph(_pdf_text(doc.recipient_email), styles["body_small"]))
    if doc.recipient_siret:
        recip_lines.append(Paragraph(f"SIRET : {_pdf_text(doc.recipient_siret)}", styles["body_small"]))

    # Meta column
    meta_lines = [Paragraph(f"Date : {doc.issue_date.strftime('%d/%m/%Y')}", styles["body_small"])]
    if doc.kind == BillingDocumentKind.DEVIS and doc.validity_until:
        meta_lines.append(
            Paragraph(
                f"Valide jusqu'au : {doc.validity_until.strftime('%d/%m/%Y')}",
                styles["body_small"],
            )
        )
    if doc.kind == BillingDocumentKind.FACTURE:
        if doc.payment_due_date:
            meta_lines.append(
                Paragraph(
                    f"Échéance : {doc.payment_due_date.strftime('%d/%m/%Y')}",
                    styles["body_small"],
                )
            )
        if doc.payment_terms:
            meta_lines.append(
                Paragraph(
                    f"Conditions : {_pdf_text(doc.payment_terms)}",
                    styles["body_small"],
                )
            )

    col_w = usable_width / 2
    data = [[recip_lines, meta_lines]]
    t = _PageTable(data, colWidths=[col_w, col_w])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    return [_split_rows_taller_than_a_page(t, usable_width)]


def _project_label(project: Optional[Project]) -> Optional[str]:
    """'Name — address' of the project the document belongs to (None when it has no project)."""
    if project is None:
        return None
    parts = [p.strip() for p in (project.name, project.address) if isinstance(p, str) and p.strip()]
    return " — ".join(dict.fromkeys(parts)) or None


def _build_object_line(project: Optional[Project], styles: dict) -> list:
    """'Objet : <project>' above the greeting — the 'mission citée en objet' it refers to."""
    label = _project_label(project)
    if not label:
        return []
    return [Paragraph(f"<b>Objet\u00a0:</b> {_pdf_text(label)}", styles["body"]), Spacer(1, 3 * mm)]


# Items-table cell padding (LEFTPADDING + RIGHTPADDING) plus a little slack for rounding.
_ITEM_CELL_PADDING = 4 + 4 + 2
# Share of the width the description column always keeps, however wide the figures get.
_MIN_DESCRIPTION_SHARE = 0.4


def _items_col_widths(header_lines: list, value_rows: list, usable_width: float) -> list:
    """Widths of the items table: Libellé first, then one per figure column.

    Each figure column is as wide as its widest header line or value, so headers and
    amounts never wrap mid-word; the description takes the rest of the width.
    """
    header_widths = []
    widths = []
    for col, lines in enumerate(header_lines):
        header_w = max(pdfmetrics.stringWidth(line, "DejaVu-Bold", 9) for line in lines) + _ITEM_CELL_PADDING
        value_w = max((pdfmetrics.stringWidth(row[col], "DejaVu", 8) for row in value_rows), default=0)
        header_widths.append(header_w)
        widths.append(max(header_w, value_w + _ITEM_CELL_PADDING))
    room = usable_width * (1 - _MIN_DESCRIPTION_SHARE)
    if sum(widths) > room:
        # Absurdly large figures: let them wrap rather than squeeze the description out,
        # but keep every header on one line.
        factor = (room - sum(header_widths)) / (sum(widths) - sum(header_widths))
        widths = [h + (w - h) * factor for h, w in zip(header_widths, widths)]
    return [usable_width - sum(widths)] + widths


def _build_items_table(
    doc: BillingDocument, styles: dict, usable_width: float, project: Optional[Project] = None
) -> list:
    """Items table mirroring the source ANN ECO CONSTRUCTION layout exactly.

    7 visible columns: Libelle / U / Qte / PU (HT) en EUR / Avancement / Montant (HT) en EUR / TVA.
    Header row uses ORANGE background (#F18728), bold, centered with thin black grid.
    When the document belongs to a project, a "project repeat row" (bold dark grey)
    is inserted right after the header.
    Section header rows (centered bold) interleave with line-item rows.
    """
    header_lines = [["U"], ["Qté"], ["PU", "(HT) en €"], ["Avancement"], ["Montant", "(HT) en €"], ["TVA"]]
    headers = [Paragraph("Libellé", styles["label_white"])] + [
        Paragraph("<br/>".join(_xml_escape(line) for line in lines), styles["label_white"]) for lines in header_lines
    ]
    figures = [
        (
            "",
            format_decimal_fr(item.quantity),
            format_unit_price_eur_fr(item.unit_price),
            "100%",
            format_eur_fr(item.total_ht),
            f"{_fmt_pct(item.vat_rate)}\u00a0%",
        )
        for item in doc.items
    ]
    col_widths = _items_col_widths(header_lines, figures, usable_width)

    table_data: list = [headers]
    section_row_idxs: list = []

    project_label = _project_label(project)
    project_row_idx = None
    if project_label:
        project_row_idx = len(table_data)
        table_data.append([Paragraph(_pdf_text(project_label), styles["h3"]), "", "", "", "", "", ""])

    headings = section_headings(item.category for item in doc.items)
    for item, heading, (unit, qty, unit_price, progress, total_ht, vat) in zip(doc.items, headings, figures):
        if heading is not None:
            section_row_idxs.append(len(table_data))
            table_data.append([Paragraph(_pdf_text(heading), styles["section_title"]), "", "", "", "", "", ""])

        table_data.append(
            [
                Paragraph(_pdf_text(item.description), styles["body_small"]),
                Paragraph(unit, styles["body_small_center"]),
                Paragraph(qty, styles["body_small_center"]),
                Paragraph(unit_price, styles["body_small_right"]),
                Paragraph(progress, styles["body_small_center"]),
                Paragraph(total_ht, styles["body_small_right"]),
                Paragraph(vat, styles["body_small_center"]),
            ]
        )

    style_cmds: list = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F18728")),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 1), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#888888")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
    ]
    if project_row_idx is not None:
        style_cmds.extend(
            [
                ("SPAN", (0, project_row_idx), (-1, project_row_idx)),
                ("FONTNAME", (0, project_row_idx), (-1, project_row_idx), "DejaVu-Bold"),
                ("FONTSIZE", (0, project_row_idx), (-1, project_row_idx), 9),
                ("ALIGN", (0, project_row_idx), (-1, project_row_idx), "LEFT"),
                ("TEXTCOLOR", (0, project_row_idx), (-1, project_row_idx), colors.HexColor("#222222")),
            ]
        )
    for sidx in section_row_idxs:
        style_cmds.extend(
            [
                ("SPAN", (0, sidx), (-1, sidx)),
                ("FONTNAME", (0, sidx), (-1, sidx), "DejaVu-Bold"),
                ("FONTSIZE", (0, sidx), (-1, sidx), 9),
                ("ALIGN", (0, sidx), (-1, sidx), "CENTER"),
                ("TOPPADDING", (0, sidx), (-1, sidx), 4),
                ("BOTTOMPADDING", (0, sidx), (-1, sidx), 4),
            ]
        )

    t = _PageTable(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    # A line whose description is taller than a page continues on the next one.
    return [_split_rows_taller_than_a_page(t, usable_width)]


def _build_totals_block(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Right-aligned totals block: HT, TVA per rate, TTC."""
    col_label_w = usable_width * 0.7
    col_value_w = usable_width * 0.3

    rows = []

    # Total HT
    rows.append(
        [
            Paragraph("Total HT", styles["body_small_right"]),
            Paragraph(format_eur_fr(doc.total_ht), styles["body_small_right"]),
        ]
    )

    # VAT per rate (sorted descending by rate)
    for rate, base_ht, tva_amt in doc.vat_breakdown:
        label = f"TVA {_fmt_pct(rate)}\u00a0%"
        rows.append(
            [
                Paragraph(label, styles["body_small_right"]),
                Paragraph(format_eur_fr(tva_amt), styles["body_small_right"]),
            ]
        )

    # Separator then TTC
    totals_table = Table(rows, colWidths=[col_label_w, col_value_w])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ]
        )
    )

    # TTC row — bold, larger
    ttc_data = [
        [
            Paragraph("Total TTC", styles["right_bold"]),
            Paragraph(format_eur_fr(doc.total_ttc), styles["right_bold"]),
        ]
    ]
    ttc_table = Table(ttc_data, colWidths=[col_label_w, col_value_w])
    ttc_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF2FA")),
            ]
        )
    )
    return [totals_table, ttc_table]


def _build_text_section(title: str, content: str, styles: dict) -> list:
    """Generic titled text block (notes / terms)."""
    elements: list = [
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")),
        Spacer(1, 3 * mm),
        Paragraph(title, styles["section_title"]),
    ]
    for line in content.splitlines():
        elements.append(Paragraph(_pdf_text(line) or " ", styles["body"]))
    return elements


def _build_bank_coords_block(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Render a 'Coordonnées bancaires' block when IBAN/BIC are present.

    Mirrors the source-PDF layout: a labelled, lightly-bordered band at the
    bottom of the doc with IBAN + BIC. Returns [] if no bank info is set.
    """
    if not doc.issuer_iban and not doc.issuer_bic:
        return []
    rows: list = [[Paragraph("COORDONNÉES BANCAIRES", styles["section_title"]), ""]]
    if doc.issuer_iban:
        rows.append(
            [
                Paragraph("IBAN", styles["label"]),
                Paragraph(_xml_escape(doc.issuer_iban), styles["body_small"]),
            ]
        )
    if doc.issuer_bic:
        rows.append(
            [
                Paragraph("BIC", styles["label"]),
                Paragraph(_xml_escape(doc.issuer_bic), styles["body_small"]),
            ]
        )
    label_w = 25 * mm
    t = Table(rows, colWidths=[label_w, usable_width - label_w])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("SPAN", (0, 0), (-1, 0)),  # title row spans both columns
                # Orange band (FFF18728) matching the source COORDONNÉES BANCAIRES header.
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F18728")),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ]
        )
    )
    return [t]


def _build_signature_block(doc: BillingDocument, styles: dict, usable_width: float) -> list:
    """Signature block: blank placeholder left, boxed signature text (with room to sign) right."""
    if not doc.signature_block_text or not doc.signature_block_text.strip():
        return []
    box: list = [
        Paragraph(_pdf_text(line) or "\u00a0", styles["body"]) for line in doc.signature_block_text.splitlines()
    ]
    box.append(Spacer(1, 20 * mm))
    col_w = usable_width / 2
    t = _PageTable([["", box]], colWidths=[col_w, col_w])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#888888")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [_split_rows_taller_than_a_page(t, usable_width)]


def _build_legal_footer_note(styles: dict) -> list:
    """Standard French construction-invoice late-payment legal note."""
    text = (
        "Indemnité forfaitaire de retard de paiement : 40 € "
        "(conformément à l'article 121-II de la loi n° 2012-387 du 22 Mars 2012 "
        "et au décret n° 2012-1115 du 2 Oct. 2012)."
    )
    return [Paragraph(_xml_escape(text), styles["body_grey"])]


def _build_greeting_block(doc: BillingDocument, styles: dict) -> list:
    """Render the standard French opening: 'Madame, Monsieur,' + 2 short paragraphs.

    Mirrors the source layout (rows 19-21 in the xlsx).
    """
    return [
        Paragraph("Madame, Monsieur,", styles["body"]),
        Spacer(1, 1 * mm),
        Paragraph(intro_sentence(doc.kind), styles["body"]),
        Paragraph(
            "Je reste à votre disposition pour toute précision ou complément d'information.",
            styles["body"],
        ),
    ]


def _build_closing_greeting(styles: dict) -> list:
    """Standard closing line above the bank coords (mirrors B71 in source xlsx)."""
    return [
        Paragraph(
            "Veuillez agréer, Madame, Monsieur, l'expression de nos salutations distinguées.",
            styles["body"],
        )
    ]


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


class _FooterCanvas(Canvas):
    """Canvas that draws 'Généré par Folio · Page X / Y' on every page.

    The page count is only known once the whole story is laid out, so each page's
    state is kept until save() and the footers are drawn then.
    """

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._page_states: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 — ReportLab API
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._page_states)
        for state in self._page_states:
            self.__dict__.update(state)
            self.saveState()
            self.setFont("DejaVu", 8)
            self.drawCentredString(A4[0] / 2, 8 * mm, f"Généré par Folio · Page {self._pageNumber} / {total}")
            self.restoreState()
            super().showPage()
        super().save()


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


class ReportLabBillingDocumentPdfRenderer:
    """Implements BillingDocumentPdfRendererPort via ReportLab Platypus.

    Instantiate once and call render() for each document.
    Font registration is done at module import time and is idempotent.
    """

    def render(self, doc: BillingDocument, project: Optional[Project] = None) -> bytes:
        """Render *doc* to a valid A4 PDF byte string.

        *project* is the project the document is linked to (doc.project_id), printed
        as the document's "Objet"; None when it has none.
        Returns raw PDF bytes starting with b"%PDF-".
        Never raises due to logo fetch errors (fail-open).
        Raises BillingDocumentRenderError when the content cannot be laid out on
        A4 pages, and other errors if ReportLab cannot build at all (missing fonts).
        """
        _register_fonts_once()  # safe no-op if already registered

        buf = BytesIO()
        pdf_doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=_MARGIN,
            rightMargin=_MARGIN,
            topMargin=_MARGIN,
            bottomMargin=_BOTTOM_MARGIN,
        )
        usable_width = A4[0] - 2 * _MARGIN
        s = _styles()

        story: list = []

        # 1. Issuer header
        story.extend(_build_issuer_header(doc, s, usable_width))
        story.append(Spacer(1, 5 * mm))

        # 2. Title row
        story.extend(_build_title_row(doc, s, usable_width))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#3B5BDB")))
        story.append(Spacer(1, 5 * mm))

        # 3. Recipient + meta
        story.extend(_build_recipient_meta(doc, s, usable_width))
        story.append(Spacer(1, 5 * mm))

        # 3b. "Objet : <project>" + greeting block — "Madame, Monsieur," + 2 short paragraphs
        story.extend(_build_object_line(project, s))
        story.extend(_build_greeting_block(doc, s))
        story.append(Spacer(1, 4 * mm))

        # 4. Items table
        if doc.items:
            story.extend(_build_items_table(doc, s, usable_width, project))
        else:
            story.append(Paragraph("Aucun article.", s["body_small"]))
        story.append(Spacer(1, 4 * mm))

        # 5. Totals block
        story.extend(_build_totals_block(doc, s, usable_width))
        story.append(Spacer(1, 6 * mm))

        # 6. Notes
        if doc.notes and doc.notes.strip():
            story.extend(_build_text_section("Notes", doc.notes, s))
            story.append(Spacer(1, 4 * mm))

        # 7. Terms / conditions générales
        if doc.terms and doc.terms.strip():
            story.extend(_build_text_section("Conditions générales", doc.terms, s))
            story.append(Spacer(1, 4 * mm))

        # 7b. Signature block
        signature_block = _build_signature_block(doc, s, usable_width)
        if signature_block:
            story.extend(signature_block)
            story.append(Spacer(1, 4 * mm))

        # 8. Closing greeting (mirrors B71 in source xlsx)
        story.extend(_build_closing_greeting(s))
        story.append(Spacer(1, 4 * mm))

        # 9. Coordonnées bancaires (if IBAN/BIC present) — orange band
        bank_block = _build_bank_coords_block(doc, s, usable_width)
        if bank_block:
            story.extend(bank_block)

        # 10. Late-payment legal note (factures only — devis don't trigger this)
        if doc.kind == BillingDocumentKind.FACTURE:
            story.append(Spacer(1, 3 * mm))
            story.extend(_build_legal_footer_note(s))

        try:
            pdf_doc.build(story, canvasmaker=_FooterCanvas)
        except LayoutError as err:  # a flowable that fits no page — never a bare 500
            raise BillingDocumentRenderError(doc.id) from err
        return buf.getvalue()
