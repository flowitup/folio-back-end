"""Invoice API request/response schemas."""

import re
from decimal import Decimal
from typing import Literal, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.v1.date_bounds import BusinessDate
from app.api.v1.numeric_bounds import MAX_LINE_QUANTITY, MAX_LINE_TOTAL, MAX_LINE_UNIT_PRICE
from app.api.v1.projects.schemas import ErrorResponse  # reuse shared error schema

InvoiceTypeLiteral = Literal["released_funds", "labor", "materials_services", "others", "return"]
SettledViaLiteral = Literal["cash", "avoir"]
HighlightColorLiteral = Literal["red", "orange", "yellow", "green", "blue", "purple"]


def normalize_invoice_type_value(value: object) -> object:
    """Map the pre-rename wire value 'refund' to its canonical 'return'.

    External clients (e.g. the folio MCP plugin) may still send 'refund';
    responses always emit 'return'.
    """
    return "return" if value == "refund" else value


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class InvoiceItemSchema(BaseModel):
    """A single line item on an invoice.

    unit_price carries no ge=0 bound — sign validation is type-dependent and
    enforced in the use-case (mixed-sign allowed for materials_services + return).
    quantity must be > 0; vat_rate is 0–100. Both are capped like billing lines,
    and inf/NaN are refused.

    Precision matches what the web and mobile forms can show and re-save: quantity and
    unit_price at most 4 decimals, vat_rate at most 2 (a stored 33.333 % blocked every
    later web edit). The line's TTC amount is capped too: quantity and price were capped
    separately, so their product could reach ~10^16 and lose cents in float maths.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0, le=float(MAX_LINE_QUANTITY))
    # sign enforcement is in the use-case, not here
    unit_price: float = Field(..., ge=-float(MAX_LINE_UNIT_PRICE), le=float(MAX_LINE_UNIT_PRICE))
    vat_rate: float = Field(default=0.0, ge=0, le=100)

    @field_validator("quantity", "unit_price")
    @classmethod
    def _at_most_four_decimals(cls, v: float) -> float:
        if _decimal_places(v) > 4:
            raise ValueError("at most 4 decimals")
        return v

    @field_validator("vat_rate")
    @classmethod
    def _at_most_two_decimals(cls, v: float) -> float:
        if _decimal_places(v) > 2:
            raise ValueError("VAT rate allows at most 2 decimals")
        return v

    @model_validator(mode="after")
    def _line_total_capped(self) -> "InvoiceItemSchema":
        ht = Decimal(str(self.quantity)) * Decimal(str(self.unit_price))
        if abs(ht * (1 + Decimal(str(self.vat_rate)) / 100)) > MAX_LINE_TOTAL:
            raise ValueError(f"Line amount exceeds the maximum of {MAX_LINE_TOTAL}")
        return self


def _items_capped(items: Optional[List[InvoiceItemSchema]]) -> Optional[List[InvoiceItemSchema]]:
    """An expense's TTC total stays under the same ceiling as each of its lines."""
    if items:
        total = sum(
            (
                Decimal(str(i.quantity)) * Decimal(str(i.unit_price)) * (1 + Decimal(str(i.vat_rate)) / 100)
                for i in items
            ),
            Decimal("0"),
        )
        if abs(total) > MAX_LINE_TOTAL:
            raise ValueError(f"Expense total exceeds the maximum of {MAX_LINE_TOTAL}")
    return items


def _decimal_places(value: float) -> int:
    """Decimals of a JSON number as the client wrote it (5.5 → 1, 33.333 → 3, 1e-05 → 5)."""
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


class CreateInvoiceSchema(BaseModel):
    """Request body for creating an invoice.

    refunds_invoice_id is optional; only valid when type='return'. When provided,
    the use-case validates that the target is a same-project materials_services invoice
    and enforces the cap (cumulative returns may not exceed the source total).
    Mixed-sign unit_price is allowed for materials_services and return types.
    service_month is optional; only valid when type='labor'. The use-case normalizes
    any day-of-month to day=1.
    settled_via/applied_to_invoice_id are optional; only valid when type='return'.
    applied_to_invoice_id additionally requires settled_via='avoir' — the use-case
    validates the target and enforces the applied-amount cap, then auto-aligns the
    return's payment_method_id to the target's.
    worker_id is optional; only valid when type='labor'. The use-case validates the
    worker belongs to this project and overrides recipient_name with a server-side
    snapshot of the worker's display name.
    """

    type: InvoiceTypeLiteral

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    issue_date: BusinessDate  # ISO date string (YYYY-MM-DD), 2000–2100
    recipient_name: str = Field(..., min_length=1, max_length=255)
    recipient_address: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=20000)
    items: List[InvoiceItemSchema] = Field(..., min_length=1, max_length=500)
    _items_total_capped = field_validator("items")(_items_capped)
    payment_method_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[BusinessDate] = None
    settled_via: Optional[SettledViaLiteral] = None
    applied_to_invoice_id: Optional[UUID] = None
    worker_id: Optional[UUID] = None
    highlight_color: Optional[HighlightColorLiteral] = None
    # Company cash advance — only valid when type='released_funds' (use-case enforced).
    is_cash_advance: bool = False


class UpdateInvoiceSchema(BaseModel):
    """Request body for partially updating an invoice.

    payment_method_id, refunds_invoice_id, service_month, settled_via,
    applied_to_invoice_id, and worker_id use exclude_unset semantics:
      - field absent  → do not touch that field
      - field = null  → clear the field
      - field = value → set to that value
    Mixed-sign unit_price is allowed for materials_services and return types.
    """

    type: Optional[InvoiceTypeLiteral] = None
    issue_date: Optional[BusinessDate] = None  # ISO date string, 2000–2100

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    recipient_address: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=20000)
    items: Optional[List[InvoiceItemSchema]] = Field(None, max_length=500)
    _items_total_capped = field_validator("items")(_items_capped)
    payment_method_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[BusinessDate] = None
    settled_via: Optional[SettledViaLiteral] = None
    applied_to_invoice_id: Optional[UUID] = None
    worker_id: Optional[UUID] = None
    highlight_color: Optional[HighlightColorLiteral] = None
    # exclude_unset semantics: absent → keep; true → set (released_funds only);
    # false or null → clear (null is accepted for clients that send nulls uniformly).
    is_cash_advance: Optional[bool] = None


_YYYY_MM = re.compile(r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?$")
_YYYY_MM_PATTERN = _YYYY_MM.pattern


class ListInvoicesFilterQuery(BaseModel):
    """Optional drill-down query filters for GET /projects/<id>/invoices.

    ``type`` keeps its pre-existing manual-parse/400 error path unchanged;
    these two are new (Labor Payments Hub) and are validated here, returning
    422 on malformed input — mirrors ExportLaborQuery's month-format pattern.
    Both compose with the existing ``type`` filter.
    """

    service_month: Optional[str] = Field(
        None,
        pattern=_YYYY_MM_PATTERN,
        description=(
            "Exact service_month match (labor invoices only). Format: YYYY-MM or "
            "YYYY-MM-DD — a day suffix is tolerated and normalized to the month, "
            "matching the YYYY-MM-01 convention the FE uses for the entity field."
        ),
    )
    worker_id: Optional[UUID] = None


class LaborPaymentsWorkerSchema(BaseModel):
    """One worker's paid total within a labor-payments month bucket."""

    worker_id: str
    worker_name: str
    paid: float
    invoice_count: int


class LaborPaymentsMonthSchema(BaseModel):
    """One (service_month) bucket of the labor-payments summary response.

    year/month are both null for the no-service_month bucket, always last.
    """

    year: Optional[int] = None
    month: Optional[int] = None
    total_paid: float
    workers: List[LaborPaymentsWorkerSchema]
    unassigned_paid: float
    unassigned_count: int
    # Flagged-method split of the bucket's paid total. Invoices with no
    # method or an unflagged one count in neither, so these may sum to less
    # than total_paid.
    company_paid: float = 0.0
    personal_paid: float = 0.0


class LaborPaymentsSummarySchema(BaseModel):
    """Response body for GET /projects/<id>/labor-payments-summary."""

    months: List[LaborPaymentsMonthSchema]


class ExportInvoicesQuery(BaseModel):
    """Pydantic v2 model for GET /invoices-export query params."""

    from_month: str = Field(alias="from")
    to_month: str = Field(alias="to")
    format: Literal["xlsx", "pdf"]
    type: Optional[InvoiceTypeLiteral] = None
    # Label language of the file; English when omitted.
    locale: Literal["en", "fr", "vi"] = "en"

    model_config = {"populate_by_name": True}

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    @field_validator("from_month", "to_month")
    @classmethod
    def _yyyy_mm(cls, v: str) -> str:
        if not _YYYY_MM.fullmatch(v):  # unlike "$", rejects a trailing "\n"
            raise ValueError("must be YYYY-MM")
        return v

    @model_validator(mode="after")
    def _range(self):
        if self.from_month > self.to_month:
            raise ValueError("from must be <= to")
        fy, fm = int(self.from_month[:4]), int(self.from_month[5:7])
        ty, tm = int(self.to_month[:4]), int(self.to_month[5:7])
        span = (ty - fy) * 12 + (tm - fm) + 1
        if span > 24:
            raise ValueError("range may not exceed 24 months")
        return self


# Re-export for convenience
__all__ = [
    "InvoiceItemSchema",
    "CreateInvoiceSchema",
    "UpdateInvoiceSchema",
    "ExportInvoicesQuery",
    "ListInvoicesFilterQuery",
    "LaborPaymentsWorkerSchema",
    "LaborPaymentsMonthSchema",
    "LaborPaymentsSummarySchema",
    "ErrorResponse",
]
