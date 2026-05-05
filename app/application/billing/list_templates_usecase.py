"""ListTemplatesUseCase — list billing document templates for a user."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing.dtos import BillingTemplateResponse
from app.application.billing.ports import BillingTemplateRepositoryPort
from app.domain.billing.enums import BillingDocumentKind


class ListTemplatesUseCase:
    """Return all templates owned by a user, optionally filtered by kind."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort) -> None:
        self._template_repo = template_repo

    def execute(
        self,
        user_id: UUID,
        kind: Optional[BillingDocumentKind] = None,
    ) -> list[BillingTemplateResponse]:
        templates = self._template_repo.list_for_user(user_id=user_id, kind=kind)
        return [BillingTemplateResponse.from_entity(t) for t in templates]
