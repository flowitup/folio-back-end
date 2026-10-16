"""QA round 3: import statuses per kind, and accent-insensitive search folding."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import column, select, table
from sqlalchemy.dialects import postgresql, sqlite

from app.api.v1.billing.schemas import ImportBillingDocumentRequest
from app.infrastructure.database.text_fold import fold_text, folded, folded_like_pattern


def _body(kind: str, status: str) -> dict:
    return {
        "kind": kind,
        "company_id": str(uuid4()),
        "recipient_name": "QA3 Client",
        "document_number": "QA3-D-0001",
        "status": status,
        "items": [{"description": "Ravalement", "quantity": "1", "unit_price": "1500", "vat_rate": "10"}],
    }


@pytest.mark.parametrize("status", ["paid", "cancelled"])
def test_devis_import_refuses_facture_only_status(status):
    with pytest.raises(ValidationError, match="not valid for a devis"):
        ImportBillingDocumentRequest.model_validate(_body("devis", status))


@pytest.mark.parametrize("status", ["draft", "sent"])
def test_devis_import_accepts_devis_status(status):
    assert ImportBillingDocumentRequest.model_validate(_body("devis", status)).status == status


@pytest.mark.parametrize("status", ["draft", "sent", "paid", "cancelled"])
def test_facture_import_accepts_its_statuses(status):
    assert ImportBillingDocumentRequest.model_validate(_body("facture", status)).status == status


def test_fold_text_strips_french_and_vietnamese_accents():
    assert fold_text("Plâtre ÉCHAFAUDAGE Lefèvre-Dupré") == "platre echafaudage lefevre-dupre"
    assert fold_text("Ống nhựa Đường") == "ong nhua duong"


def test_folded_like_pattern_escapes_wildcards():
    assert folded_like_pattern("100%_É") == "%100\\%\\_e%"


def test_folded_compiles_to_translate_on_postgres_and_lower_elsewhere():
    t = table("x", column("name"))
    stmt = select(folded(t.c.name))
    pg = str(stmt.compile(dialect=postgresql.dialect()))
    assert "lower(translate(x.name, '" in pg
    lite = str(stmt.compile(dialect=sqlite.dialect()))
    assert "lower(x.name)" in lite
