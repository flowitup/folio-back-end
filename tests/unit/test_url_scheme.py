"""Stored URL fields are http(s) only: a javascript:/data:/tel: value is one tap from running in a viewer."""

import pytest

from app.api.v1.bibliotheque.schemas import ImportRecordSchema, ImportRequestSchema
from app.api.v1.chiffrage.schemas import QuoteCreateBody, QuoteUpdateBody, StoreCreateBody, StoreUpdateBody
from app.api.v1.url_scheme import lenient_http_url, normalize_http_url

BAD = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,x",
    "tel:+33102030405",
    "myapp://open?x=1",
    "ftp://x.fr/a",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
    # A browser ignores tabs, newlines and other control characters inside or before the scheme.
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "\x01javascript:alert(1)",
    " \x00 data:text/html,x",
    "jav\r\nascript:alert(1)",
    "da\x00ta:text/html,x",
    "vb\tscript:msgbox(1)",
    "fi\nle:///etc/passwd",
]


@pytest.mark.parametrize("value", BAD)
def test_normalize_refuses_other_schemes(value):
    assert normalize_http_url(value) is None
    assert normalize_http_url(value, any_bare_domain=True) is None


def test_normalize_keeps_http_and_prefixes_www():
    assert normalize_http_url("https://a.fr/p?x=1") == "https://a.fr/p?x=1"
    assert normalize_http_url("  http://a.fr ") == "http://a.fr"
    assert normalize_http_url("www.a.fr/p") == "https://www.a.fr/p"
    assert normalize_http_url("a.fr/p") is None
    assert normalize_http_url("a.fr/p", any_bare_domain=True) == "https://a.fr/p"
    assert normalize_http_url("localhost:8080/p", any_bare_domain=True) is None
    assert normalize_http_url(None) is None
    assert normalize_http_url("   ") is None


@pytest.mark.parametrize("value", BAD)
def test_lenient_turns_other_schemes_into_none(value):
    assert lenient_http_url(500)(value) is None


@pytest.mark.parametrize("value", ["Leroy Merlin", "leroy merlin.fr", "magasin du coin", "12345", "http", "://x"])
def test_lenient_turns_free_text_into_none(value):
    assert lenient_http_url(500)(value) is None


def test_lenient_keeps_links_prefixes_domains_and_clears_blank():
    check = lenient_http_url(500)
    assert check(None) is None
    assert check("") is None
    assert check("   ") is None
    assert check("example.com") == "https://example.com"
    assert check("leroymerlin.fr/p/1") == "https://leroymerlin.fr/p/1"
    assert check("www.example.com") == "https://www.example.com"
    assert check("https://example.com") == "https://example.com"
    assert check("localhost:8080/p") is None  # no dot: not a domain we would link to
    assert check(12) == 12  # not a string: the field refuses it


def test_lenient_drops_a_value_only_the_https_prefix_made_too_long():
    domain = "a.fr/" + "p" * 490  # 495 characters, 503 with the scheme
    assert lenient_http_url(500)(domain) is None
    assert lenient_http_url(500)("https://a.fr/" + "p" * 487) == "https://a.fr/" + "p" * 487  # exactly 500
    too_long = "https://a.fr/" + "p" * 600
    assert lenient_http_url(500)(too_long) == too_long  # still the field's own max_length 422


@pytest.mark.parametrize("value", BAD + ["Leroy Merlin"])
def test_chiffrage_bodies_store_no_link_for_other_schemes_or_free_text(value):
    assert StoreCreateBody(name="S", website_url=value).website_url is None
    assert StoreUpdateBody(website_url=value).website_url is None
    assert QuoteCreateBody(unit_price_ht="1", supplier_name="S", product_url=value).product_url is None
    assert QuoteUpdateBody(product_url=value).product_url is None


def test_chiffrage_bodies_accept_http_and_null():
    assert StoreCreateBody(name="S", website_url="https://a.fr").website_url == "https://a.fr"
    assert StoreCreateBody(name="S", website_url="www.a.fr").website_url == "https://www.a.fr"
    assert StoreUpdateBody(website_url=None).website_url is None
    assert QuoteUpdateBody(product_url="a.fr/p").product_url == "https://a.fr/p"


def _record(**over):
    base = {
        "supplier_reference": "R1",
        "product_name": "Vis",
        "quantity": "1",
        "unit_price": "1",
        "purchased_at": "2026-01-01T00:00:00+00:00",
        "source_document_ref": "T1",
        "source_document_type": "ticket",
        "line_index": 0,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("value", BAD + ["x" * 600])
def test_import_record_drops_unusable_product_url(value):
    assert ImportRecordSchema(**_record(product_url=value)).product_url is None


def test_import_record_keeps_http_url():
    assert ImportRecordSchema(**_record(product_url="https://a.fr/p/1")).product_url == "https://a.fr/p/1"
    assert ImportRecordSchema(**_record(product_url="www.a.fr/p/1")).product_url == "https://www.a.fr/p/1"
    assert ImportRecordSchema(**_record(product_url="leroymerlin.fr/p/1")).product_url == "https://leroymerlin.fr/p/1"


def test_import_request_urls():
    from uuid import uuid4

    def build(**over):
        return ImportRequestSchema(
            company_id=str(uuid4()),
            supplier_name="Acme",
            supplier_slug="acme",
            records=[_record()],
            **over,
        )

    req = build(supplier_website_url="javascript:alert(1)", supplier_product_url_template="tel:123")
    assert req.supplier_website_url is None
    assert req.supplier_product_url_template is None
    req = build(supplier_website_url="https://a.fr", supplier_product_url_template="https://a.fr/p/{reference}")
    assert req.supplier_website_url == "https://a.fr"
    assert req.supplier_product_url_template == "https://a.fr/p/{reference}"
    req = build(supplier_website_url="leroymerlin.fr", supplier_product_url_template="leroymerlin.fr/p/{reference}")
    assert req.supplier_website_url == "https://leroymerlin.fr"
    assert req.supplier_product_url_template == "https://leroymerlin.fr/p/{reference}"
    req = build(supplier_website_url="Leroy Merlin", supplier_product_url_template="java\tscript:{reference}")
    assert req.supplier_website_url is None and req.supplier_product_url_template is None
