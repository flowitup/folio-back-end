"""``FeatureHandlers`` — the real implementation of ``FeatureHandlersPort`` (phase 03/04).

Composes ``TicketFeature`` (feature C), ``MaterialFeature`` (feature A) and
``InvoiceFetchFeature`` (feature B). Replaces
``app.application.assistant.service.DefaultFeatureHandlers`` in every real wiring
(``app/__init__.py``, ``tests/conftest.py``); the default stays for callers that only
exercise the router/equipment slice and do not care about features A/B/C.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.assistant.features.invoice_fetch import InvoiceFetchFeature
from app.application.assistant.features.material import MaterialFeature
from app.application.assistant.features.ticket import TicketFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import RouterDecision


class FeatureHandlers:
    """Implements ``FeatureHandlersPort`` (structurally — no explicit inheritance needed)."""

    def __init__(self, *, ticket: TicketFeature, material: MaterialFeature, invoice_fetch: InvoiceFetchFeature) -> None:
        self._ticket = ticket
        self._material = material
        self._invoice_fetch = invoice_fetch

    def identify_material(
        self, *, user_id: UUID, message_id: UUID, lang: str, messenger: AssistantMessenger, trace_id: str
    ) -> None:
        self._material.run(user_id=user_id, message_id=message_id, lang=lang, messenger=messenger, trace_id=trace_id)

    def import_ticket(
        self, *, user_id: UUID, message_id: UUID, lang: str, messenger: AssistantMessenger, trace_id: str
    ) -> None:
        self._ticket.run(user_id=user_id, message_id=message_id, lang=lang, messenger=messenger, trace_id=trace_id)

    def fetch_invoice(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        decision: RouterDecision,
    ) -> None:
        self._invoice_fetch.fetch_invoice(
            user_id=user_id, message_id=message_id, lang=lang, messenger=messenger, trace_id=trace_id, decision=decision
        )

    def handle_action(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        action: str,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> bool:
        if self._ticket.handle_action(
            user_id=user_id,
            message_id=message_id,
            action=action,
            payload=payload,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        ):
            return True
        if self._material.handle_action(
            user_id=user_id,
            message_id=message_id,
            action=action,
            payload=payload,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        ):
            return True
        return self._invoice_fetch.handle_action(
            user_id=user_id,
            message_id=message_id,
            action=action,
            payload=payload,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        )


__all__ = ["FeatureHandlers", "TicketFeature", "MaterialFeature", "InvoiceFetchFeature"]
