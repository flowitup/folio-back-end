"""The project spend fields leave the API rounded to cents, like the invoice ledger."""

from decimal import Decimal

from app.api.v1.projects.routes import _spend_fields
from app.application.projects.ports import ProjectSpent


def test_spend_fields_are_rounded_half_up_to_cents():
    rollup = ProjectSpent(
        total=Decimal("7735.598"),
        invoiced=Decimal("7735.598"),
        by_credits=Decimal("1210.26"),
        personal=Decimal("6525.338"),
        labor_accrued=Decimal("150.375"),
        labor_paid=Decimal("100"),
        labor_unpaid=Decimal("50.375"),
        personal_by_type={"materials_services": Decimal("395.388"), "labor": Decimal("0.005")},
    )

    fields = _spend_fields(rollup)

    assert fields == {
        "spent": 7735.60,
        "spent_invoiced": 7735.60,
        "spent_by_credits": 1210.26,
        "spent_personal": 6525.34,
        "labor_accrued": 150.38,
        "labor_paid": 100.0,
        "labor_unpaid": 50.38,
        "personal_by_type": {"materials_services": 395.39, "labor": 0.01},
    }
