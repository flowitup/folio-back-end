"""CreateTemplateUseCase — create a new billing document template."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.application.billing._helpers import _items_from_inputs
from app.application.billing.dtos import BillingTemplateResponse, CreateTemplateInput
from app.application.billing.ports import BillingTemplateRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingTemplateNameConflictError
from app.domain.billing.template import BillingDocumentTemplate


class CreateTemplateUseCase:
    """Create a new billing document template owned by the caller."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort) -> None:
        self._template_repo = template_repo

    def execute(
        self,
        inp: CreateTemplateInput,
        db_session: TransactionalSessionPort,
    ) -> BillingTemplateResponse:
        name = inp.name.strip() if inp.name else ""
        if not name:
            raise ValueError("Template name is required")

        items = _items_from_inputs(inp.items) if inp.items else ()

        now = datetime.now(timezone.utc)
        template = BillingDocumentTemplate(
            id=uuid4(),
            user_id=inp.user_id,
            kind=inp.kind,
            name=name,
            notes=inp.notes,
            terms=inp.terms,
            default_vat_rate=inp.default_vat_rate,
            items=items,
            created_at=now,
            updated_at=now,
        )

        try:
            saved = self._template_repo.save(template)
            db_session.commit()
        except IntegrityError as exc:
            # M2: unique constraint (user_id, kind, name) violated → 409, not 500
            raise BillingTemplateNameConflictError(name) from exc
        return BillingTemplateResponse.from_entity(saved)
