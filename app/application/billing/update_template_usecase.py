"""UpdateTemplateUseCase — partial update of a billing document template."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.application.billing._helpers import _assert_billing_template_write_access, _items_from_inputs
from app.application.billing.dtos import CLEARABLE_TEMPLATE_FIELDS, BillingTemplateResponse, UpdateTemplateInput
from app.application.billing.ports import BillingTemplateRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingTemplateNameConflictError, BillingTemplateNotFoundError


class UpdateTemplateUseCase:
    """Partially update a billing document template.

    Immutable fields: id, user_id, kind, created_at.
    Applies only fields explicitly set (not None) in the input DTO, and clears
    the optional fields named in `inp.cleared`.
    A company admin of the template's company may update it (the author, for a
    template with no company).
    """

    def __init__(self, template_repo: BillingTemplateRepositoryPort, access_repo=None) -> None:
        self._template_repo = template_repo
        self._access_repo = access_repo

    def execute(
        self,
        inp: UpdateTemplateInput,
        db_session: TransactionalSessionPort,
    ) -> BillingTemplateResponse:
        template = self._template_repo.find_by_id(inp.id)
        if template is None:
            raise BillingTemplateNotFoundError(inp.id)
        _assert_billing_template_write_access(template, inp.user_id, self._access_repo)

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

        for field_name in inp.cleared & CLEARABLE_TEMPLATE_FIELDS:
            updates[field_name] = None

        updated = template.with_updates(**updates)
        try:
            saved = self._template_repo.save(updated)
            db_session.commit()
        except IntegrityError as exc:
            # Renaming onto a name already used (company, author, kind) → 409, not 500
            raise BillingTemplateNameConflictError(updated.name) from exc
        return BillingTemplateResponse.from_entity(saved)
