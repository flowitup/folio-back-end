"""ListTemplatesUseCase — list billing document templates for a user."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing.dtos import BillingTemplateResponse
from app.application.billing.ports import BillingTemplateRepositoryPort, attached_company_ids
from app.domain.billing.enums import BillingDocumentKind


class ListTemplatesUseCase:
    """Return templates, optionally filtered by kind.

    `company_id=None` (default) preserves the pre-Phase-2 behavior: only the
    caller's own templates. `company_id` set switches to every template
    scoped to that company (any author) — the company-wide listing the
    route uses when the caller explicitly asks for one (see
    `app.api.v1.billing.templates_routes.list_billing_templates`). The
    caller's own company templates are listed only while they are still
    attached to that company.
    """

    def __init__(self, template_repo: BillingTemplateRepositoryPort, access_repo=None) -> None:
        self._template_repo = template_repo
        self._access_repo = access_repo

    def execute(
        self,
        user_id: UUID,
        kind: Optional[BillingDocumentKind] = None,
        company_id: Optional[UUID] = None,
        company_ids: Optional[list[UUID]] = None,
    ) -> list[BillingTemplateResponse]:
        """`company_ids` (used when `company_id` is None): also list every template of those
        companies, after the caller's own — the companies the caller administers, for a
        client that names no company."""
        if company_id is not None:
            templates = self._template_repo.list_for_company(company_id=company_id, kind=kind)
        else:
            templates = self._own_templates(user_id, kind)
            seen = {t.id for t in templates}
            for cid in company_ids or []:
                for t in self._template_repo.list_for_company(company_id=cid, kind=kind):
                    if t.id not in seen:
                        seen.add(t.id)
                        templates.append(t)
        return [BillingTemplateResponse.from_entity(t) for t in templates]

    def _own_templates(self, user_id: UUID, kind: Optional[BillingDocumentKind]) -> list:
        """The caller's own templates, minus those of companies they have left."""
        templates = self._template_repo.list_for_user(user_id=user_id, kind=kind)
        attached = attached_company_ids(self._access_repo, user_id)
        if attached is not None:
            templates = [t for t in templates if t.company_id is None or t.company_id in attached]
        return list(templates)
