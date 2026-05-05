"""UpdateTemplateUseCase — partial update of a billing document template."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import _items_from_inputs
from app.application.billing.dtos import BillingTemplateResponse, UpdateTemplateInput
from app.application.billing.ports import BillingTemplateRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingTemplateNotFoundError, ForbiddenBillingDocumentError


class UpdateTemplateUseCase:
    """Partially update a billing document template.

    Immutable fields: id, user_id, kind, created_at.
    Applies only fields explicitly set (not None) in the input DTO.
    """

    def __init__(self, template_repo: BillingTemplateRepositoryPort) -> None:
        self._template_repo = template_repo

    def execute(
        self,
        inp: UpdateTemplateInput,
        db_session: TransactionalSessionPort,
    ) -> BillingTemplateResponse:
        template = self._template_repo.find_by_id(inp.id)
        if template is None:
            raise BillingTemplateNotFoundError(inp.id)
        if template.user_id != inp.user_id:
            raise ForbiddenBillingDocumentError(inp.id)

        updates: dict = {"updated_at": datetime.now(timezone.utc)}

        if inp.name is not None:
            name = inp.name.strip()
            if not name:
                raise ValueError("Template name is required")
            updates["name"] = name

        if inp.items is not None:
            updates["items"] = _items_from_inputs(inp.items) if inp.items else ()

        if inp.notes is not None:
            updates["notes"] = inp.notes

        if inp.terms is not None:
            updates["terms"] = inp.terms

        if inp.default_vat_rate is not None:
            updates["default_vat_rate"] = inp.default_vat_rate

        updated = template.with_updates(**updates)
        saved = self._template_repo.save(updated)
        db_session.commit()
        return BillingTemplateResponse.from_entity(saved)
