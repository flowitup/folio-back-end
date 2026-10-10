"""ImportBillingDocumentUseCase — import a historical billing document verbatim.

Differences from CreateBillingDocumentUseCase:
  - Accepts a pre-supplied document_number (no auto-generation).
  - Accepts explicit status (e.g. PAID for historical imports).
  - Accepts optional created_at to preserve original timestamp.
  - Calls bump_to_at_least on the counter when doc number parses to year+seq
    (seq up to _MAX_COUNTER_SEQ), so subsequent auto-creates continue from a
    sane sequence.
  - Wraps IntegrityError on unique-constraint violation into
    BillingDocumentAlreadyExistsError (409).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from uuid import uuid4

from app.application.billing._helpers import (
    _compute_default_payment_due_date,
    _compute_default_validity_until,
    _effective_prefix_from_company,
    _items_from_inputs,
    _snapshot_issuer_from_company,
)
from app.application.billing.dtos import BillingDocumentResponse, ImportBillingDocumentInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    CompanyRepositoryPort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
    assert_company_admin,
    assert_project_read_access,
    assert_user_company_access,
)
from app.domain.billing.dates import validate_document_dates, validate_kind_fields
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.numbering import next_document_number
from app.domain.billing.exceptions import (
    BillingDocumentAlreadyExistsError,
    MissingCompanyProfileError,
)
from app.domain.time import business_today


# Regex to detect document numbers that encode year + sequence.
# Matches e.g. FAC2025001, FAC-2025-001, DEV-2026-00003, FLW-FACTURE-2026-007.
# Skips truly irregular numbers like FAC0026-ANN-2025-11/08 (extra trailing tokens).
# Groups: year (4 digits), seq (trailing digits after optional dash).
_DOC_NUMBER_PATTERN = re.compile(r"^(?:[A-Za-z]+-?)+(?P<year>\d{4})-?(?P<seq>\d+)$")

# Largest imported sequence that moves the counter. A longer number is not a
# sequence the counter could continue from (e.g. FAC202403151230459, a timestamp
# from other software, parses as year 2024 + a 10-digit sequence): bumping the
# INTEGER counter to it overflowed (a 500), or, just below the limit, made every
# later facture of that year fail. Such a document is still imported verbatim.
_MAX_COUNTER_SEQ = 999_999

# Unique constraint name for the (company_id, kind, document_number) partial index.
_UNIQUE_CONSTRAINT = "uix_billing_document_company_kind_number"


def _parse_year_seq(document_number: str) -> tuple[int, int] | None:
    """Return (year, seq) if document_number matches the year+seq pattern, else None."""
    m = _DOC_NUMBER_PATTERN.match(document_number)
    if m is None:
        return None
    return int(m.group("year")), int(m.group("seq"))


class ImportBillingDocumentUseCase:
    """Import a historical billing document with a pre-supplied number.

    Pre-conditions:
      - company_id is required; user must be an admin of that company, the
        same rule as creating a document.
      - project_id, when given, must be a project the user can read.
      - At least one line item required.
      - document_number is accepted verbatim (1..32 chars, non-empty after strip).
      - Duplicate (company_id, kind, document_number) → BillingDocumentAlreadyExistsError.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        project_repo: ProjectReadPort | None = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._counter_repo = counter_repo
        self._company_repo = company_repo
        self._access_repo = access_repo
        self._project_repo = project_repo

    def execute(
        self,
        inp: ImportBillingDocumentInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. An imported document may only link a project the caller can read
        # (company admins read every project of their company, as on create).
        assert_project_read_access(self._project_repo, inp.project_id, inp.user_id, self._access_repo)

        # 2. company_id required — validate attachment and snapshot issuer
        if inp.company_id is None:
            raise MissingCompanyProfileError(inp.user_id)

        company = assert_user_company_access(self._access_repo, self._company_repo, inp.user_id, inp.company_id)
        if company is None:
            raise MissingCompanyProfileError(inp.user_id)

        # Importing writes the company's billing, exactly like creating: admins only.
        assert_company_admin(self._access_repo, inp.user_id, inp.company_id)

        issuer_snapshot = _snapshot_issuer_from_company(company)

        # 2. Validate items (≥1)
        if not inp.items:
            raise ValueError("At least one line item is required")
        items = _items_from_inputs(inp.items)

        # 3. Validate recipient name
        recipient_name = inp.recipient_name.strip() if inp.recipient_name else ""
        if not recipient_name:
            raise ValueError("Recipient name is required")

        # 4. Validate document_number
        doc_number = inp.document_number.strip() if inp.document_number else ""
        if not doc_number:
            raise ValueError("document_number is required")
        if len(doc_number) > 32:
            raise ValueError("document_number exceeds 32 characters")

        validate_kind_fields(inp.kind, inp.validity_until, inp.payment_due_date, inp.payment_terms)

        # 5. Bump counter if doc number parses to year+seq
        parsed = _parse_year_seq(doc_number)
        if parsed is not None:
            year, seq = parsed
            if seq <= _MAX_COUNTER_SEQ:
                self._counter_repo.bump_to_at_least(inp.company_id, inp.kind, year, seq)
            elif doc_number == next_document_number(
                prefix_override=_effective_prefix_from_company(company) or "",
                kind=inp.kind,
                year=year,
                sequence=seq,
            ):
                # A number in Folio's own format beyond the counter's range would be handed
                # out again by the counter later on, and every new document of that year
                # would then fail on the duplicate.
                raise ValueError(f"document_number sequence must not exceed {_MAX_COUNTER_SEQ}")

        # 6. Resolve timestamps
        now = datetime.now(timezone.utc)
        created_at = inp.created_at if inp.created_at is not None else now
        issue_date = inp.issue_date if inp.issue_date is not None else business_today(now)
        validate_document_dates(issue_date, inp.validity_until, inp.payment_due_date)

        # 7. Build domain entity — resolve kind-specific optional dates
        validity_until = inp.validity_until
        payment_due_date = inp.payment_due_date
        if inp.kind == BillingDocumentKind.DEVIS and validity_until is None:
            validity_until = _compute_default_validity_until(issue_date)
        if inp.kind == BillingDocumentKind.FACTURE and payment_due_date is None:
            payment_due_date = _compute_default_payment_due_date(issue_date)

        # Resolve payment_terms
        payment_terms = inp.payment_terms
        if payment_terms is None and inp.kind == BillingDocumentKind.FACTURE:
            payment_terms = company.default_payment_terms

        doc = BillingDocument(
            id=uuid4(),
            user_id=inp.user_id,
            company_id=inp.company_id,
            kind=inp.kind,
            document_number=doc_number,
            status=inp.status,
            issue_date=issue_date,
            created_at=created_at,
            updated_at=created_at,
            recipient_name=recipient_name,
            items=items,
            project_id=inp.project_id,
            recipient_address=inp.recipient_address,
            recipient_email=inp.recipient_email,
            recipient_siret=inp.recipient_siret,
            notes=inp.notes,
            terms=inp.terms,
            signature_block_text=inp.signature_block_text,
            validity_until=validity_until,
            payment_due_date=payment_due_date,
            payment_terms=payment_terms,
            **issuer_snapshot,
        )

        # 8. Persist — wrap IntegrityError on unique-constraint violation
        try:
            with db_session.begin_nested():
                saved = self._doc_repo.save(doc)
            db_session.commit()
        except IntegrityError as exc:
            orig = str(exc.orig) if exc.orig else str(exc)
            if _UNIQUE_CONSTRAINT in orig or "unique" in orig.lower():
                raise BillingDocumentAlreadyExistsError(inp.company_id, inp.kind.value, doc_number) from exc
            raise

        return BillingDocumentResponse.from_entity(saved)
