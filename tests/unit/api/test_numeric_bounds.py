"""Oversized money/quantity inputs are refused at validation, and responses stay valid JSON."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from flask import Flask
from pydantic import BaseModel, ValidationError

from app.api._helpers.json_provider import FiniteJSONProvider
from app.api.v1.bibliotheque.schemas import ImportRecordSchema
from app.api.v1.chiffrage.schemas import ArticleCreateBody, ArticleUpdateBody, QuoteCreateBody, QuoteUpdateBody
from app.api.v1.inventory.schemas import CreateInventoryItemSchema, UpdateInventoryItemSchema
from app.api.v1.invoices.schemas import InvoiceItemSchema
from app.api.v1.labor.rate_change_schemas import CreateRateChangeRequest
from app.api.v1.labor.schemas import (
    BulkLogAttendanceEntry,
    CreateWorkerRequest,
    LogAttendanceRequest,
    UpdateAttendanceRequest,
)
from app.api.v1.projects.schemas import CreateProjectRequest, UpdateProjectRequest
from app.application.invoice.dtos import money


def _field_errors(schema: type[BaseModel], field: str, value: object) -> list[dict]:
    with pytest.raises(ValidationError) as exc:
        schema.model_validate({field: value})
    return [e for e in exc.value.errors() if e["loc"] == (field,)]


@pytest.mark.parametrize(
    ("schema", "field", "value"),
    [
        (CreateProjectRequest, "budget", "1000000000000"),
        (UpdateProjectRequest, "budget", "1000000000000"),
        (CreateWorkerRequest, "daily_rate", 1e9),
        (LogAttendanceRequest, "amount_override", 1e9),
        (BulkLogAttendanceEntry, "amount_override", 1e9),
        (UpdateAttendanceRequest, "amount_override", 1e9),
        (CreateRateChangeRequest, "daily_rate", 1e12),
        (CreateInventoryItemSchema, "quantity", 2**40),
        (UpdateInventoryItemSchema, "quantity", 2**40),
        (ArticleCreateBody, "quantity", "1e30"),
        (ArticleUpdateBody, "quantity", "1e30"),
        (QuoteCreateBody, "unit_price_ht", "1e20"),
        (QuoteUpdateBody, "unit_price_ht", "1e20"),
        (ImportRecordSchema, "quantity", "1e20"),
        (ImportRecordSchema, "unit_price", "1e20"),
        (InvoiceItemSchema, "quantity", 1e8),
        (InvoiceItemSchema, "unit_price", 1e308),
        (InvoiceItemSchema, "unit_price", -1e308),
    ],
)
def test_oversized_value_is_a_validation_error(schema, field, value):
    errors = _field_errors(schema, field, value)
    assert errors, f"{schema.__name__}.{field} accepted {value!r}"
    assert errors[0]["type"] in ("less_than_equal", "greater_than_equal")


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_invoice_line_refuses_non_finite_numbers(value):
    assert _field_errors(InvoiceItemSchema, "unit_price", value)


def test_largest_allowed_expense_line_is_accepted():
    item = InvoiceItemSchema(description="x", quantity=9_999_999, unit_price=999_999_999)
    assert item.unit_price == 999_999_999


def test_money_handles_amounts_beyond_28_digits():
    assert money(Decimal("1e30")) == 1e30


def test_json_responses_never_contain_infinity_or_nan():
    app = Flask(__name__)
    app.json = FiniteJSONProvider(app)
    with app.app_context():
        body = app.json.response({"spent": float("inf"), "rows": [float("nan"), 1.5], "ok": Decimal("2.50")})
    text = body.get_data(as_text=True)
    assert "Infinity" not in text and "NaN" not in text
    assert json.loads(text) == {"spent": None, "rows": [None, 1.5], "ok": "2.50"}
