"""GetTemplateUseCase — fetch a single billing document template by ID."""

from __future__ import annotations

from uuid import UUID

from app.application.billing._helpers import _assert_billing_template_access
from app.application.billing.dtos import BillingTemplateResponse
from app.application.billing.ports import BillingTemplateRepositoryPort
from app.domain.billing.exceptions import BillingTemplateNotFoundError


class GetTemplateUseCase:
    """Fetch a billing document template by UUID (author or company admin)."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort, access_repo=None) -> None:
        self._template_repo = template_repo
        self._access_repo = access_repo

    def execute(self, template_id: UUID, user_id: UUID) -> BillingTemplateResponse:
        template = self._template_repo.find_by_id(template_id)
        if template is None:
            raise BillingTemplateNotFoundError(template_id)
        _assert_billing_template_access(template, user_id, self._access_repo)
        return BillingTemplateResponse.from_entity(template)
