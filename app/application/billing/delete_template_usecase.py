"""DeleteTemplateUseCase — hard-delete a billing document template."""

from __future__ import annotations

from uuid import UUID

from app.application.billing.ports import BillingTemplateRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingTemplateNotFoundError, ForbiddenBillingDocumentError


class DeleteTemplateUseCase:
    """Hard-delete a billing document template with ownership check."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort) -> None:
        self._template_repo = template_repo

    def execute(
        self,
        template_id: UUID,
        user_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        template = self._template_repo.find_by_id(template_id)
        if template is None:
            raise BillingTemplateNotFoundError(template_id)
        if template.user_id != user_id:
            raise ForbiddenBillingDocumentError(template_id)
        self._template_repo.delete(template_id)
        db_session.commit()
