"""DeleteTemplateUseCase — hard-delete a billing document template."""

from __future__ import annotations

from uuid import UUID

from app.application.billing._helpers import _assert_billing_template_access
from app.application.billing.ports import BillingTemplateRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingTemplateNotFoundError


class DeleteTemplateUseCase:
    """Hard-delete a billing document template (author or company admin)."""

    def __init__(self, template_repo: BillingTemplateRepositoryPort, access_repo=None) -> None:
        self._template_repo = template_repo
        self._access_repo = access_repo

    def execute(
        self,
        template_id: UUID,
        user_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        template = self._template_repo.find_by_id(template_id)
        if template is None:
            raise BillingTemplateNotFoundError(template_id)
        _assert_billing_template_access(template, user_id, self._access_repo)
        self._template_repo.delete(template_id)
        db_session.commit()
