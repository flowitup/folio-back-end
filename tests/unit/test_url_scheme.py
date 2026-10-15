"""Stored URL fields are http(s) only: a javascript:/data:/tel: value is one tap from running in a viewer."""

import pytest

from app.api.v1.bibliotheque.schemas import ImportRecordSchema, ImportRequestSchema
from app.api.v1.chiffrage.schemas import QuoteCreateBody, QuoteUpdateBody, StoreCreateBody, StoreUpdateBody
from app.api.v1.url_scheme import normalize_http_url, require_http_url

BAD = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,x",
    "tel:+33102030405",
    "myapp://open?x=1",
    "ftp://x.fr/a",
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
def test_require_raises_on_other_schemes(value):
    with pytest.raises(ValueError):
        require_http_url(value)


def test_require_passes_none_and_blank_through():
    assert require_http_url(None) is None
    assert require_http_url("") == ""
    assert require_http_url("example.com") == "https://example.com"
    assert require_http_url("https://example.com") == "https://example.com"


@pytest.mark.parametrize("value", BAD)
def test_chiffrage_bodies_refuse_other_schemes(value):
    with pytest.raises(ValueError):
        StoreCreateBody(name="S", website_url=value)
    with pytest.raises(ValueError):
        StoreUpdateBody(website_url=value)
    with pytest.raises(ValueError):
        QuoteCreateBody(unit_price_ht="1", supplier_name="S", product_url=value)
    with pytest.raises(ValueError):
        QuoteUpdateBody(product_url=value)


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
