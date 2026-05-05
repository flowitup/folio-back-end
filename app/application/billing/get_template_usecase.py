"""GetTemplateUseCase — fetch a single billing document template by ID."""

from __future__ import annotations

from uuid import UUID

from app.application.billing.dtos import BillingTemplateResponse
from app.application.billing.ports import BillingTemplateRepositoryPort
from app.domain.billing.exceptions import BillingTemplateNotFoundError, ForbiddenBillingDocumentError


class GetTemplateUseCase:
    """Fetch a billing document template by UUID with ownership check."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort) -> None:
        self._template_repo = template_repo

    def execute(self, template_id: UUID, user_id: UUID) -> BillingTemplateResponse:
        template = self._template_repo.find_by_id(template_id)
        if template is None:
            raise BillingTemplateNotFoundError(template_id)
        if template.user_id != user_id:
            raise ForbiddenBillingDocumentError(template_id)
        return BillingTemplateResponse.from_entity(template)
