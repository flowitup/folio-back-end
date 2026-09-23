"""Persistence port for ``assistant_audit_log`` (D17 layer 4: one row per handled
mention, readable from the admin channel and the web supervision page).

Kept out of ``ports.py`` (the provider-facing module) since nothing in phase 01/02 writes
to it yet — the pipeline hooks that call ``AssistantAuditPort.add`` land with the
redaction/ChannelScope work (phase 03/04). This module only stands up the storage shape
those phases build on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Protocol
from uuid import UUID


@dataclass(frozen=True)
class AuditLogEntry:
    """One row of ``assistant_audit_log``."""

    id: UUID
    company_id: Optional[UUID]
    channel_key: str
    user_id: Optional[UUID]
    message_id: Optional[UUID]
    intent: Optional[str]
    feature: Optional[str]
    tools: Optional[Any]
    outcome: Optional[str]
    refused_reason: Optional[str]
    cost_usd: Decimal
    trace_id: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class UserAuditCount:
    """One user's row/refusal counts for a company/period — the "who asked what"
    weekly summary's own shape, aggregated in SQL rather than computed by loading
    rows client-side, so it is never silently capped by ``list_for_company``'s own
    ``limit`` for a busy company."""

    user_id: Optional[UUID]
    total: int
    refused: int


class AssistantAuditPort(Protocol):
    """Persistence contract for the assistant's supervision log."""

    def add(
        self,
        *,
        company_id: Optional[UUID],
        channel_key: str,
        user_id: Optional[UUID],
        message_id: Optional[UUID],
        intent: Optional[str],
        feature: Optional[str],
        tools: Optional[Any] = None,
        outcome: Optional[str],
        refused_reason: Optional[str] = None,
        cost_usd: Decimal = Decimal("0"),
        trace_id: Optional[str] = None,
    ) -> AuditLogEntry:
        """Insert one audit row for a handled (or refused) mention."""
        ...

    def list_for_company(
        self,
        company_id: UUID,
        *,
        from_: Optional[datetime] = None,
        to: Optional[datetime] = None,
        user_id: Optional[UUID] = None,
        limit: int = 200,
    ) -> list[AuditLogEntry]:
        """Rows for a company's admin channel / supervision page, newest first.

        ``from_``/``to`` bound ``created_at`` (either end optional); ``user_id`` narrows
        to one asker's rows when given.
        """
        ...

    def count_by_user_for_company(
        self, company_id: UUID, *, from_: Optional[datetime] = None, to: Optional[datetime] = None
    ) -> list[UserAuditCount]:
        """Per-user ``(total, refused)`` counts for the period, ``GROUP BY user_id`` in
        SQL — the weekly "who asked what" admin-channel answer
        (``AdminAnswersFeature.ask_audit``) should use this instead of ``list_for_company``
        plus a client-side tally, which silently undercounts once a busy company has more
        than ``limit`` rows in the window.
        """
        ...


__all__ = ["AssistantAuditPort", "AuditLogEntry", "UserAuditCount"]
