"""Business dates outside 2000-2100 are refused at validation (no 500, no 'FAC-1-001')."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from app.api.v1.billing.schemas import (
    ApplyTemplateRequest,
    ConvertRequest,
    CreateBillingDocumentRequest,
    ImportBillingDocumentRequest,
    UpdateBillingDocumentRequest,
)
from app.api.v1.date_bounds import MAX_BUSINESS_DATE, MIN_BUSINESS_DATE
from app.api.v1.invoices.schemas import CreateInvoiceSchema, UpdateInvoiceSchema
from app.api.v1.labor.rate_change_schemas import CreateRateChangeRequest
from app.api.v1.tasks.schemas import CreateTaskSchema, UpdateTaskSchema


def _field_errors(schema: type[BaseModel], field: str, value: object) -> list[dict]:
    with pytest.raises(ValidationError) as exc:
        schema.model_validate({field: value})
    return [e for e in exc.value.errors() if e["loc"] == (field,)]


DATE_FIELDS = [
    (CreateInvoiceSchema, "issue_date"),
    (CreateInvoiceSchema, "service_month"),
    (UpdateInvoiceSchema, "issue_date"),
    (UpdateInvoiceSchema, "service_month"),
    (CreateBillingDocumentRequest, "issue_date"),
    (CreateBillingDocumentRequest, "validity_until"),
    (CreateBillingDocumentRequest, "payment_due_date"),
    (UpdateBillingDocumentRequest, "issue_date"),
    (UpdateBillingDocumentRequest, "validity_until"),
    (UpdateBillingDocumentRequest, "payment_due_date"),
    (ConvertRequest, "payment_due_date"),
    (ApplyTemplateRequest, "issue_date"),
    (ImportBillingDocumentRequest, "issue_date"),
    (ImportBillingDocumentRequest, "validity_until"),
    (ImportBillingDocumentRequest, "payment_due_date"),
    (CreateTaskSchema, "due_date"),
    (UpdateTaskSchema, "due_date"),
]


@pytest.mark.parametrize(("schema", "field"), DATE_FIELDS)
@pytest.mark.parametrize("value", ["0001-01-15", "1999-12-31", "2101-01-01", "9999-12-31"])
def test_out_of_range_date_is_a_validation_error(schema, field, value):
    errors = _field_errors(schema, field, value)
    assert errors, f"{schema.__name__}.{field} accepted {value!r}"
    assert "between 2000-01-01 and 2100-12-31" in errors[0]["msg"]


@pytest.mark.parametrize(("schema", "field"), DATE_FIELDS)
@pytest.mark.parametrize("value", [MIN_BUSINESS_DATE.isoformat(), "2026-10-09", MAX_BUSINESS_DATE.isoformat()])
def test_in_range_date_is_accepted(schema, field, value):
    try:
        schema.model_validate({field: value})
    except ValidationError as exc:
        assert not [e for e in exc.errors() if e["loc"] == (field,)], exc.errors()


def test_update_can_still_clear_an_optional_date():
    assert UpdateInvoiceSchema.model_validate({"service_month": None}).service_month is None
    assert UpdateTaskSchema.model_validate({"due_date": None}).due_date is None


def test_rate_change_effective_date_stays_unbounded():
    # Owner decision: a worker's rate change can be backdated to any date.
    req = CreateRateChangeRequest.model_validate({"daily_rate": 100, "effective_date": "1990-01-01"})
    assert req.effective_date == date(1990, 1, 1)
