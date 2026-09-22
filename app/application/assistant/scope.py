"""D17 layer 1 — confidential classes and redaction.

Two classes of data are confidential to a company's `admin:<company_id>` channel:
``finance_company`` (released funds, budget, remaining, income, billing document
amounts/status) and ``payroll`` (daily rate, rate history, salaries, labor payment
summaries, per-worker totals, a worker's unit price). Every other channel
(``company``/``project``) follows the asker's own permissions — D19.

``redact()`` is the single enforcement point every ``AssistantMessenger.post_*`` call
goes through (via ``ChannelScope`` — see ``messages.py``): it recursively strips any key
classified into a class the scope does not allow, plus any key matching one of the
catch-all suffixes below, from a card/choice/job_status payload. Text bodies are not
walked here — a free-text DeepSeek reply is covered instead by the output guard
(``gate.output_guard_triggered`` + the Jev Noul check in ``service.py``), since there is
no fixed field name to strip out of prose.

``scope=None`` fails CLOSED: it is treated exactly like a non-admin scope (every
confidential class withheld), never like "no redaction". ``scope`` is a required keyword
on every ``post_*`` (see ``messages.py``), so this only matters for a caller that
explicitly has no channel context — it must never be read as permission to skip
redaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

if TYPE_CHECKING:
    from app.application.assistant.models import ChannelScope
    from app.application.authz.ports import AuthzReaderPort
    from app.application.companies.ports import UserCompanyAccessRepositoryPort

#: `finance_company` — released funds, budget, remaining, income, billing amounts/status.
#: Deliberately excludes `total_ht`/`total_ttc`/`total_amount`: those are a SUPPLIER
#: invoice's own total (ticket/material/invoice-fetch cards) — D19 project spend, visible
#: to every member, never classified. Only the CLIENT billing document's own amount/
#: status fields (`billing_*`) are confidential.
FINANCE_COMPANY_FIELDS: frozenset[str] = frozenset(
    {
        "released_funds",
        "released_total",
        "funds_released",
        "funds_released_total",
        "company_released",
        "personal_released",
        "cash_advance_total",
        "budget",
        "budget_total",
        "budget_source",
        "remaining",
        "remaining_total",
        "income",
        "income_total",
        "company_income",
        "spent_total",
        "company_spent",
        "personal_spent",
        "spent_split",
        "billing_amount",
        "billing_status",
        "billing_total_ht",
        "billing_total_ttc",
        "amount_due",
        "balance",
        "due_total",
    }
)

#: `payroll` — a worker's rate, rate history, salary totals, labor payment summaries.
PAYROLL_FIELDS: frozenset[str] = frozenset(
    {
        "daily_rate",
        "hourly_rate",
        "rate",
        "rate_history",
        "rate_changes",
        "salary",
        "salary_total",
        "worker_total",
        "unit_price",
        "labor_payment_total",
        "paid_total",
        "worker_paid",
        "company_paid",
        "personal_paid",
        "unassigned_paid",
    }
)

CLASS_FIELDS: dict[str, frozenset[str]] = {
    "finance_company": FINANCE_COMPANY_FIELDS,
    "payroll": PAYROLL_FIELDS,
}

#: The only two confidential classes — an ``allowed_classes`` of everything (both) means
#: "admin channel, nothing withheld"; empty means "withhold both" (every non-admin scope).
CONFIDENTIAL_CLASSES: frozenset[str] = frozenset(CLASS_FIELDS)

#: Catch-all suffixes: a key ending in one of these is dropped regardless of its exact
#: name (covers a card/choice's own bespoke field names without having to enumerate
#: every one of them in CLASS_FIELDS above).
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_rate",
    "salary",
    "budget",
    "released",
    "remaining",
    "income",
    "total_paid",
    "total_due",
)


def allowed_classes_for(is_admin_channel: bool) -> frozenset[str]:
    """All confidential classes for an admin channel, none otherwise (D17)."""
    return CONFIDENTIAL_CLASSES if is_admin_channel else frozenset()


def channel_company_ids(
    scope: "ChannelScope",
    user_id: UUID,
    company_access: "UserCompanyAccessRepositoryPort",
    authz_reader: "Optional[AuthzReaderPort]" = None,
) -> list[UUID]:
    """Company ids a channel dispatch may search or act on — the channel's OWN company
    only, never any other company the asker also happens to belong to. Shared by
    ``AssistantService`` (router/equipment/S0 state) and the ticket/material/
    invoice-fetch features (NEW-H1): every one of them used to enumerate the asker's
    full ``UserCompanyAccessRepositoryPort.list_for_user`` and could offer, act on, or
    push another tenant's project/company/invoice into a channel scoped to just one.

    A channel with no company (``scope.company_id is None``) always returns ``[]`` —
    there is nothing to bound to. Platform ops are bounded by the channel the same way,
    but without needing a ``user_company_access`` row of their own — an ops user often
    has none. Every other caller is intersected with real memberships, so a channel
    member who somehow lost their company-access row still gets nothing (fail-closed).
    """
    if scope.company_id is None:
        return []
    if authz_reader is not None and authz_reader.is_platform_ops(user_id):
        return [scope.company_id]
    memberships = {access.company_id for access in company_access.list_for_user(user_id)}
    return [scope.company_id] if scope.company_id in memberships else []


def _blocked_field_names(allowed_classes: frozenset[str]) -> frozenset[str]:
    names: set[str] = set()
    for class_name, fields in CLASS_FIELDS.items():
        if class_name not in allowed_classes:
            names.update(fields)
    return frozenset(names)


def _is_sensitive_key(key: str, blocked_names: frozenset[str]) -> bool:
    key_lower = key.lower()
    if key_lower in blocked_names:
        return True
    return any(key_lower.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _strip(value: Any, blocked_names: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v, blocked_names) for k, v in value.items() if not _is_sensitive_key(k, blocked_names)}
    if isinstance(value, list):
        return [_strip(v, blocked_names) for v in value]
    return value


def redact(scope: "ChannelScope | None", payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strips every field classified into a class ``scope`` does not allow.

    ``scope=None`` (a caller with no channel context) fails CLOSED — treated exactly
    like a non-admin scope, so both confidential classes are withheld. This is defense
    in depth only: ``scope`` is a required keyword on every ``AssistantMessenger.post_*``
    (see ``messages.py``), so a caller can no longer omit it by accident; explicitly
    passing ``scope=None`` must still never be read as "skip redaction". A payload of
    ``None`` (every ``text`` message) passes through unchanged; there is nothing to walk.
    """
    if payload is None:
        return payload
    allowed_classes = scope.allowed_classes if scope is not None else frozenset()
    blocked_names = _blocked_field_names(allowed_classes)
    if not blocked_names:
        return payload
    stripped = _strip(payload, blocked_names)
    assert isinstance(stripped, dict)  # `payload` is a dict, and `_strip` preserves that shape
    return stripped


__all__ = [
    "CLASS_FIELDS",
    "CONFIDENTIAL_CLASSES",
    "FINANCE_COMPANY_FIELDS",
    "PAYROLL_FIELDS",
    "allowed_classes_for",
    "channel_company_ids",
    "redact",
]
