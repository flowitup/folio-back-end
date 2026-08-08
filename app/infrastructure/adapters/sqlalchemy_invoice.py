"""SQLAlchemy implementation of invoice repository."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.invoice.dtos import money
from app.application.invoice.ports import IInvoiceRepository, LaborPaymentsMonthRow, LaborPaymentsWorkerRow
from app.domain.entities.invoice import Invoice, InvoiceType, RefundableStatus
from app.domain.exceptions.invoice_exceptions import (
    InvoiceNotFoundError,
    InvoiceNumberConflictError,
)
from app.domain.value_objects.invoice_item import InvoiceItem
from app.infrastructure.database.invoice_spend_rules import (
    is_company_paid,
    items_total,
    load_company_paid_method_ids,
    load_personal_method_ids,
)
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.worker import WorkerModel


def _items_to_jsonb(items: list) -> list:
    """Serialize InvoiceItem list to JSONB-compatible dicts (floats, not Decimals).

    Note: 'total' is intentionally omitted — it is always computed from
    quantity * unit_price * (1 + vat_rate/100) at read time, so storing it
    wastes space and risks drift.
    """
    return [
        {
            "description": item.description,
            "quantity": float(item.quantity),
            "unit_price": float(item.unit_price),
            "vat_rate": float(item.vat_rate),
        }
        for item in items
    ]


def _jsonb_to_items(raw: list) -> List[InvoiceItem]:
    """Deserialize JSONB dicts back to InvoiceItem value objects.

    Legacy rows without 'vat_rate' default to 0 (no VAT), preserving
    backward compatibility for all pre-existing invoices.
    """
    return [
        InvoiceItem(
            description=r["description"],
            quantity=Decimal(str(r["quantity"])),
            unit_price=Decimal(str(r["unit_price"])),
            vat_rate=Decimal(str(r.get("vat_rate", 0))),
        )
        for r in (raw or [])
    ]


# TTC line-total math lives in the shared rules module so the projects-list spend
# breakdown computes invoice worth identically. Aliased to keep the private name
# used throughout this adapter.
_items_total = items_total


def _project_company_id(session: Session, project_id: UUID) -> Optional[UUID]:
    """Return the company_id owning project_id, or None if the project has no company."""
    from app.infrastructure.database.models.project import ProjectModel

    row = session.query(ProjectModel.company_id).filter_by(id=project_id).first()
    return row[0] if row and row[0] else None


def _model_to_entity(m: InvoiceModel) -> Invoice:
    """Map ORM model to domain entity."""
    return Invoice(
        id=m.id,
        project_id=m.project_id,
        invoice_number=m.invoice_number,
        type=InvoiceType(m.type),
        issue_date=m.issue_date,
        recipient_name=m.recipient_name,
        recipient_address=m.recipient_address,
        notes=m.notes,
        items=_jsonb_to_items(m.items),
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
        payment_method_id=m.payment_method_id,
        payment_method_label=m.payment_method_label,
        source_billing_document_id=m.source_billing_document_id,
        is_auto_generated=m.is_auto_generated or False,
        tag_id=m.tag_id,
        refundable_status=m.refundable_status,
        refunded_by=m.refunded_by,
        refunds_invoice_id=m.refunds_invoice_id,
        service_month=m.service_month,
        settled_via=m.settled_via,
        applied_to_invoice_id=m.applied_to_invoice_id,
        worker_id=m.worker_id,
    )


class SQLAlchemyInvoiceRepository(IInvoiceRepository):
    """SQLAlchemy adapter for invoice persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, invoice: Invoice) -> Invoice:
        model = InvoiceModel(
            id=invoice.id,
            project_id=invoice.project_id,
            invoice_number=invoice.invoice_number,
            type=invoice.type.value,
            issue_date=invoice.issue_date,
            recipient_name=invoice.recipient_name,
            recipient_address=invoice.recipient_address,
            notes=invoice.notes,
            items=_items_to_jsonb(invoice.items),
            created_by=invoice.created_by,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
            payment_method_id=invoice.payment_method_id,
            payment_method_label=invoice.payment_method_label,
            source_billing_document_id=invoice.source_billing_document_id,
            is_auto_generated=invoice.is_auto_generated,
            tag_id=invoice.tag_id,
            refundable_status=invoice.refundable_status,
            refunded_by=invoice.refunded_by,
            refunds_invoice_id=invoice.refunds_invoice_id,
            service_month=invoice.service_month,
            settled_via=invoice.settled_via,
            applied_to_invoice_id=invoice.applied_to_invoice_id,
            worker_id=invoice.worker_id,
        )
        self._session.add(model)
        try:
            self._session.commit()
        except IntegrityError as e:
            self._session.rollback()
            # Unique constraint on (project_id, invoice_number) was violated due
            # to a concurrent request generating the same sequential number.
            if "uq_project_invoice_number" in str(e.orig):
                raise InvoiceNumberConflictError("Invoice number conflict, please retry") from e
            raise
        return _model_to_entity(model)

    def find_by_id(self, invoice_id: UUID) -> Optional[Invoice]:
        model = self._session.query(InvoiceModel).filter_by(id=invoice_id).first()
        return _model_to_entity(model) if model else None

    def list_by_project(
        self,
        project_id: UUID,
        invoice_type: Optional[InvoiceType] = None,
        tag_id: Optional[UUID] = None,
        service_month: Optional[date] = None,
        worker_id: Optional[UUID] = None,
    ) -> List[Invoice]:
        query = self._session.query(InvoiceModel).filter(InvoiceModel.project_id == project_id)
        if invoice_type is not None:
            query = query.filter(InvoiceModel.type == invoice_type.value)
        if tag_id is not None:
            query = query.filter(InvoiceModel.tag_id == tag_id)
        if service_month is not None:
            query = query.filter(InvoiceModel.service_month == service_month)
        if worker_id is not None:
            query = query.filter(InvoiceModel.worker_id == worker_id)
        # Order by the invoice (issue) date, newest first; fall back to
        # creation time so same-date invoices keep a stable, deterministic order.
        models = query.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.created_at.desc()).all()
        return [_model_to_entity(m) for m in models]

    def update(self, invoice: Invoice) -> Invoice:
        model = self._session.query(InvoiceModel).filter_by(id=invoice.id).first()
        if not model:
            raise InvoiceNotFoundError(f"Invoice {invoice.id} not found")
        model.type = invoice.type.value
        model.recipient_name = invoice.recipient_name
        model.recipient_address = invoice.recipient_address
        model.issue_date = invoice.issue_date
        model.notes = invoice.notes
        model.items = _items_to_jsonb(invoice.items)
        model.updated_at = datetime.now(timezone.utc)
        model.payment_method_id = invoice.payment_method_id
        model.payment_method_label = invoice.payment_method_label
        model.tag_id = invoice.tag_id
        model.refundable_status = invoice.refundable_status
        model.refunded_by = invoice.refunded_by
        model.refunds_invoice_id = invoice.refunds_invoice_id
        model.service_month = invoice.service_month
        model.settled_via = invoice.settled_via
        model.applied_to_invoice_id = invoice.applied_to_invoice_id
        model.worker_id = invoice.worker_id
        self._session.commit()
        return _model_to_entity(model)

    def delete(self, invoice_id: UUID) -> bool:
        result = self._session.query(InvoiceModel).filter_by(id=invoice_id).delete()
        self._session.commit()
        return result > 0

    def find_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
        type_filter: Optional[InvoiceType] = None,
    ) -> List[Invoice]:
        """Return invoices where issue_date ∈ [date_from, date_to], optionally filtered by type.

        items is stored as a JSONB column (not a relationship), so there is no N+1 risk here.
        """
        q = (
            self._session.query(InvoiceModel)
            .filter(InvoiceModel.project_id == project_id)
            .filter(InvoiceModel.issue_date >= date_from)
            .filter(InvoiceModel.issue_date <= date_to)
        )
        if type_filter is not None:
            q = q.filter(InvoiceModel.type == type_filter.value)
        rows = q.order_by(InvoiceModel.issue_date, InvoiceModel.invoice_number).all()
        return [_model_to_entity(r) for r in rows]

    def next_funds_release_number(self, project_id: UUID, year: Optional[int] = None) -> str:
        """Generate next sequential funds-release number: FR-YYYY-NNNN.

        year defaults to the current UTC year when omitted, preserving the
        existing facture-driven behaviour. Bank-refund releases pass the
        SOURCE expense's issue_date year instead, so the FR number matches the
        year the expense actually happened (live path and migration backfill
        stay in parity).
        """
        if year is None:
            year = datetime.now(timezone.utc).year
        prefix = f"FR-{year}-"
        last = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.invoice_number.like(f"{prefix}%"),
            )
            .order_by(InvoiceModel.invoice_number.desc())
            .first()
        )
        n = 1
        if last:
            try:
                n = int(last.invoice_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{n:04d}"

    def get_labor_payments_summary(self, project_id: UUID) -> List[LaborPaymentsMonthRow]:
        """Aggregate paid amounts per (service_month, worker) for type='labor' invoices.

        items is JSONB — summed in Python via the shared items_total rule, same as
        every other spend aggregation in this adapter. worker_name mirrors the
        labor summary precedent (sqlalchemy_labor_entry.py): COALESCE(persons.name,
        workers.name) via an outer join, so a linked worker with no Person still
        resolves to its own name. NULL worker_id (never linked, or SET NULL after
        the linked worker was deleted) rolls into the bucket's unassigned total.
        """
        display_name = func.coalesce(PersonModel.name, WorkerModel.name)
        rows = (
            self._session.query(
                InvoiceModel.service_month,
                InvoiceModel.worker_id,
                display_name.label("worker_name"),
                InvoiceModel.items,
            )
            .outerjoin(WorkerModel, InvoiceModel.worker_id == WorkerModel.id)
            .outerjoin(PersonModel, WorkerModel.person_id == PersonModel.id)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.type == InvoiceType.LABOR.value,
            )
            .all()
        )

        zero = Decimal("0")
        # Per-bucket worker totals: {(year, month) | None: {worker_id: [name, paid, count]}}
        worker_totals: dict = {}
        # Per-bucket unassigned totals: {(year, month) | None: [paid, count]}
        unassigned_totals: dict = {}

        for service_month, worker_id, worker_name, items in rows:
            key = (service_month.year, service_month.month) if service_month is not None else None
            amount = items_total(items)

            if worker_id is None:
                bucket = unassigned_totals.setdefault(key, [zero, 0])
                bucket[0] += amount
                bucket[1] += 1
            else:
                per_worker = worker_totals.setdefault(key, {})
                entry = per_worker.setdefault(worker_id, [worker_name, zero, 0])
                entry[1] += amount
                entry[2] += 1

        all_keys = set(worker_totals) | set(unassigned_totals)
        # Most-recent month first; the no-service_month bucket (key=None) always last.
        month_keys = sorted((k for k in all_keys if k is not None), reverse=True)
        ordered_keys = month_keys + ([None] if None in all_keys else [])

        result: List[LaborPaymentsMonthRow] = []
        for key in ordered_keys:
            workers = [
                LaborPaymentsWorkerRow(worker_id=wid, worker_name=name, paid=paid, invoice_count=count)
                for wid, (name, paid, count) in sorted(worker_totals.get(key, {}).items(), key=lambda kv: kv[1][0])
            ]
            u_paid, u_count = unassigned_totals.get(key, [zero, 0])
            result.append(
                LaborPaymentsMonthRow(
                    year=key[0] if key is not None else None,
                    month=key[1] if key is not None else None,
                    workers=workers,
                    unassigned_paid=u_paid,
                    unassigned_count=u_count,
                )
            )
        return result

    def delete_by_source_billing_document_id(self, source_doc_id: UUID) -> bool:
        result = (
            self._session.query(InvoiceModel)
            .filter(InvoiceModel.source_billing_document_id == source_doc_id)
            .delete(synchronize_session=False)
        )
        self._session.commit()
        return result > 0

    def find_bank_refund_release(self, source_id: UUID) -> Optional[Invoice]:
        """Return the auto-generated bank-refund release linked to source_id, or None.

        Matches only type == 'released_funds' AND refunds_invoice_id == source_id
        AND is_auto_generated is True — see IInvoiceRepository.find_bank_refund_release.
        """
        model = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
                InvoiceModel.refunds_invoice_id == source_id,
                InvoiceModel.is_auto_generated.is_(True),
            )
            .first()
        )
        return _model_to_entity(model) if model else None

    def delete_bank_refund_release(self, source_id: UUID) -> None:
        """Delete the auto-generated bank-refund release linked to source_id, if any.

        Matches only type == 'released_funds' AND refunds_invoice_id == source_id
        AND is_auto_generated is True — see IInvoiceRepository.delete_bank_refund_release.
        """
        (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
                InvoiceModel.refunds_invoice_id == source_id,
                InvoiceModel.is_auto_generated.is_(True),
            )
            .delete(synchronize_session=False)
        )
        self._session.commit()

    def sum_funds_released(self, project_id: UUID) -> Decimal:
        """Sum total_amount for all released_funds invoices in a project.

        items is JSONB — we compute in Python to stay DB-agnostic.
        """
        rows = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
            )
            .all()
        )
        total = Decimal("0")
        for m in rows:
            total += _items_total(m.items)
        return total

    def sum_funds_released_split(self, project_id: UUID) -> tuple[Decimal, Decimal]:
        """Split sum_funds_released into (company_total, personal_total).

        personal_total: released_funds invoices whose payment_method_id belongs to a
        method flagged is_personal_payment. company_total: every other released_funds
        invoice (company-flagged, unflagged, or NULL payment_method_id). Soft-deleted
        (is_active=false) personal-payment methods still count, mirroring
        sum_company_spent. items is JSONB — computed in Python to stay DB-agnostic.

        Invariant: company_total + personal_total == sum_funds_released(project_id).
        """
        company_id = _project_company_id(self._session, project_id)
        personal_paid_ids: set[UUID] = set()
        if company_id is not None:
            personal_paid_ids = load_personal_method_ids(self._session, [company_id]).get(company_id, set())

        rows = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
            )
            .all()
        )
        company_total = Decimal("0")
        personal_total = Decimal("0")
        for m in rows:
            amount = _items_total(m.items)
            if m.payment_method_id is not None and m.payment_method_id in personal_paid_ids:
                personal_total += amount
            else:
                company_total += amount
        return company_total, personal_total

    def sum_spent_split(self, project_id: UUID) -> tuple[Decimal, Decimal]:
        """Compute (company_spent_total, personal_spent_total) in a single scan.

        Merges the sum_company_spent and sum_personal_spent rules clause-by-clause
        over one query of the project's non-released_funds invoices instead of two
        (each method previously issued its own identical scan). Each bucket keeps
        its own independent rule set and floor-at-0 exactly as documented on
        sum_company_spent / sum_personal_spent — see those docstrings for the
        full per-bucket rules (refund-nets-down, is_active ignored, etc.).
        """
        company_id = _project_company_id(self._session, project_id)
        company_paid_ids: set[UUID] = set()
        personal_paid_ids: set[UUID] = set()
        if company_id is not None:
            company_paid_ids = load_company_paid_method_ids(self._session, [company_id]).get(company_id, set())
            personal_paid_ids = load_personal_method_ids(self._session, [company_id]).get(company_id, set())

        rows = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.type != InvoiceType.RELEASED_FUNDS.value,
            )
            .all()
        )
        company_total = Decimal("0")
        personal_total = Decimal("0")
        for m in rows:
            # Company bucket: same predicate as sum_company_spent's is_company_paid gate.
            if is_company_paid(
                payment_method_id=m.payment_method_id,
                refundable_status=m.refundable_status,
                refunded_by=m.refunded_by,
                company_paid_ids=company_paid_ids,
            ):
                company_total += _items_total(m.items)

            # Personal bucket: same predicate as sum_personal_spent (personal-flagged
            # method, excluding company-reimbursed rows; bank-refunded still counts).
            if m.payment_method_id is not None and m.payment_method_id in personal_paid_ids:
                company_reimbursed = m.refundable_status == "refunded" and m.refunded_by != "bank"
                if not company_reimbursed:
                    personal_total += _items_total(m.items)

        # Never report a negative spent total; refunds can exceed spend per bucket.
        return max(company_total, Decimal("0")), max(personal_total, Decimal("0"))

    def sum_company_spent(self, project_id: UUID) -> Decimal:
        """Sum amounts the company spent (net) directly on a project.

        Counts any non-released_funds invoice where either:
          - refundable_status == 'refunded' AND refunded_by != 'bank'
            (the company itself reimbursed the expense — bank-refunded rows are
            the bank's money, not company spend; NULL refunded_by is legacy and
            counts as company), OR
          - payment_method_id belongs to a method flagged is_company_payment
            (invoice was paid directly with company funds, any non-released_funds type).

        Refund-type invoices follow the SAME company-payment rule: a refund the
        company itself issues (paid via a company-flagged method) carries negative
        line amounts, so it nets the total DOWN — the company got that money back.
        A supplier refund (no company-flagged method) fails the is_company_paid
        gate and is ignored here; it only affects the signed total-expenses figure
        computed elsewhere.

        The result is floored at 0: company refunds can exceed what the company
        spent (e.g. a refund of a client-fronted expense that was never counted
        here), but a negative "spent by company" is meaningless for the KPI.

        Soft-deleted (is_active=false) company-payment methods still count — the
        expense occurred and should not vanish from the total if a method is later
        deactivated.  items is JSONB — we compute in Python to stay DB-agnostic.

        Thin delegate over sum_spent_split, which computes both buckets in one scan.
        """
        company_total, _ = self.sum_spent_split(project_id)
        return company_total

    def sum_personal_spent(self, project_id: UUID) -> Decimal:
        """Sum amounts spent personally (out-of-pocket, non-company) on a project.

        Counts non-released_funds invoices paid via a method flagged
        is_personal_payment, EXCLUDING rows where the company already reimbursed
        the expense (refundable_status == 'refunded' AND refunded_by != 'bank';
        NULL refunded_by is legacy and counts as company-refunded, same convention
        as sum_company_spent). Bank-refunded rows (refunded_by == 'bank') still
        count — the bank's money is not the company's.

        Refund-type invoices follow the same personal-payment gate: a refund paid
        out via a personal-flagged method carries negative line amounts, so it
        nets the total down. Result is floored at 0.

        Soft-deleted (is_active=false) personal-payment methods still count — the
        expense occurred and should not vanish from the total if a method is later
        deactivated. items is JSONB — computed in Python to stay DB-agnostic.

        Thin delegate over sum_spent_split, which computes both buckets in one scan.
        """
        _, personal_total = self.sum_spent_split(project_id)
        return personal_total

    def sum_refunds_for_source(self, source_id: UUID, exclude_invoice_id: "UUID | None" = None) -> Decimal:
        """Sum total_amount of all refund invoices linked to source_id.

        Only counts invoices of type 'refund' whose refunds_invoice_id == source_id.
        When exclude_invoice_id is provided, that invoice's own row is excluded
        from the sum — used on update to avoid self-double-counting.
        items is JSONB — computed in Python to stay DB-agnostic.
        """
        query = self._session.query(InvoiceModel).filter(
            InvoiceModel.type == InvoiceType.RETURN.value,
            InvoiceModel.refunds_invoice_id == source_id,
        )
        if exclude_invoice_id is not None:
            query = query.filter(InvoiceModel.id != exclude_invoice_id)
        rows = query.all()
        total = Decimal("0")
        for m in rows:
            total += _items_total(m.items)
        return total

    def sum_applied_for_target(self, target_id: UUID, exclude_invoice_id: "UUID | None" = None) -> Decimal:
        """Sum total_amount of all avoir return invoices applied to target_id.

        Only counts invoices of type 'return' whose applied_to_invoice_id == target_id.
        When exclude_invoice_id is provided, that invoice's own row is excluded from the
        sum — used on update to avoid self-double-counting.
        items is JSONB — computed in Python to stay DB-agnostic.
        """
        query = self._session.query(InvoiceModel).filter(
            InvoiceModel.type == InvoiceType.RETURN.value,
            InvoiceModel.applied_to_invoice_id == target_id,
        )
        if exclude_invoice_id is not None:
            query = query.filter(InvoiceModel.id != exclude_invoice_id)
        rows = query.all()
        total = Decimal("0")
        for m in rows:
            total += _items_total(m.items)
        return total

    def applied_return_summaries(self, invoice_ids: list[UUID]) -> dict[UUID, list[dict]]:
        """Return {target_invoice_id: [{"invoice_number": str, "total_amount": float}, ...]}.

        Reverse lookup powering 'paid_with_returns': for each id in invoice_ids, the
        avoir return invoices whose applied_to_invoice_id points at it. Single batch
        query (one IN clause) regardless of page size; short-circuits on empty input to
        avoid emitting an invalid ``IN ()`` clause.
        """
        if not invoice_ids:
            return {}
        rows = (
            self._session.query(
                InvoiceModel.applied_to_invoice_id,
                InvoiceModel.invoice_number,
                InvoiceModel.items,
            )
            .filter(
                InvoiceModel.type == InvoiceType.RETURN.value,
                InvoiceModel.applied_to_invoice_id.in_(invoice_ids),
            )
            .all()
        )
        result: dict[UUID, list[dict]] = {}
        for target_id, invoice_number, items in rows:
            result.setdefault(target_id, []).append(
                {"invoice_number": invoice_number, "total_amount": money(_items_total(items))}
            )
        return result

    def refund_source_ids(self, source_ids: list[UUID]) -> set[UUID]:
        """Return the subset of source_ids that have ≥1 linked refund invoice.

        Single DISTINCT query over refunds_invoice_id; short-circuits on empty
        input to avoid emitting an invalid ``IN ()`` clause.
        """
        if not source_ids:
            return set()
        rows = (
            self._session.query(InvoiceModel.refunds_invoice_id)
            .filter(
                InvoiceModel.type == InvoiceType.RETURN.value,
                InvoiceModel.refunds_invoice_id.in_(source_ids),
            )
            .distinct()
            .all()
        )
        return {r[0] for r in rows if r[0] is not None}

    def bank_refund_release_numbers(self, source_ids: list[UUID]) -> dict[UUID, str]:
        """Return {source_id: invoice_number} for auto-generated bank-refund releases.

        Single query over (refunds_invoice_id, invoice_number) filtered to
        type == 'released_funds' AND is_auto_generated IS TRUE; short-circuits
        on empty input to avoid emitting an invalid ``IN ()`` clause. The
        partial unique index on refunds_invoice_id (uq_invoices_bank_refund_release)
        guarantees at most one release per source, so no DISTINCT is needed.
        """
        if not source_ids:
            return {}
        rows = (
            self._session.query(InvoiceModel.refunds_invoice_id, InvoiceModel.invoice_number)
            .filter(
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
                InvoiceModel.is_auto_generated.is_(True),
                InvoiceModel.refunds_invoice_id.in_(source_ids),
            )
            .all()
        )
        return {source_id: number for source_id, number in rows if source_id is not None}

    def list_materials_services_by_companies(
        self,
        company_ids: list[UUID],
        refundable: Optional[bool],
        limit: int,
        offset: int,
        all_companies: bool = False,
    ) -> tuple[list[dict], int]:
        """Return paginated materials_services invoices across all projects of given companies.

        Single JOIN to projects avoids N+1 for project_name. Filters by company ownership
        and invoice type; optionally filters by refundable_status presence.

        all_companies=True bypasses the company_id filter (superadmin view).
        """
        from app.infrastructure.database.models.project import ProjectModel

        query = (
            self._session.query(InvoiceModel, ProjectModel.name.label("project_name"))
            .join(ProjectModel, InvoiceModel.project_id == ProjectModel.id)
            .filter(
                InvoiceModel.type == InvoiceType.MATERIALS_SERVICES.value,
                # Always require a company — projects with no company are excluded
                # from cross-company expense tracking regardless of caller scope.
                ProjectModel.company_id.isnot(None),
            )
        )

        if not all_companies:
            query = query.filter(ProjectModel.company_id.in_(company_ids))

        if refundable is True:
            query = query.filter(InvoiceModel.refundable_status.isnot(None))
        elif refundable is False:
            query = query.filter(InvoiceModel.refundable_status.is_(None))

        total: int = query.count()

        rows = (
            query.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Collect invoice ids from this page so we can batch-load their attachments
        # in a single query, avoiding N+1 round-trips.
        page_invoice_ids = [inv_model.id for inv_model, _ in rows]

        # Build a {invoice_id -> [attachment dict, ...]} map. Newest-first with an
        # id tiebreaker — mirrors the per-invoice attachments endpoint so the same
        # invoice lists files in the same order on every surface.
        from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel

        attachments_by_invoice: dict = {}
        if page_invoice_ids:
            att_rows = (
                self._session.query(InvoiceAttachmentModel)
                .filter(InvoiceAttachmentModel.invoice_id.in_(page_invoice_ids))
                .order_by(
                    InvoiceAttachmentModel.uploaded_at.desc(),
                    InvoiceAttachmentModel.id.desc(),
                )
                .all()
            )
            for att in att_rows:
                key = att.invoice_id
                attachments_by_invoice.setdefault(key, []).append(
                    {
                        "id": str(att.id),
                        "filename": att.filename,
                        "mime_type": att.mime_type,
                        "size_bytes": att.size_bytes,
                    }
                )

        result = []
        for inv_model, project_name in rows:
            entity = _model_to_entity(inv_model)
            result.append(
                {
                    "id": str(entity.id),
                    "project_id": str(entity.project_id),
                    "project_name": project_name,
                    "invoice_number": entity.invoice_number,
                    "recipient_name": entity.recipient_name,
                    "issue_date": entity.issue_date.isoformat(),
                    "total_amount": float(entity.total_amount),
                    "refundable_status": entity.refundable_status,
                    "refunded_by": entity.refunded_by,
                    "attachments": attachments_by_invoice.get(inv_model.id, []),
                }
            )
        return result, total

    def materials_services_refund_summary(
        self,
        company_ids: list[UUID],
        all_companies: bool = False,
    ) -> dict:
        """Aggregate refund totals over the FULL materials_services filter set.

        Mirrors the exact company-scope/type filters of list_materials_services_by_companies
        (type=materials_services, ProjectModel.company_id.isnot(None), company scope,
        refundable_status.isnot(None)) but is not paginated — it sums over every matching
        invoice, not just one page.

        Line-total math mirrors InvoiceItem.total (Invoice.total_amount): sum of
        quantity * unit_price * (1 + vat_rate/100), computed in Decimal since items
        are stored as JSONB and cannot be summed in SQL portably.

        Returns floats keyed:
          refundable_amount    — status in ('refundable', 'refund_pending')
          refunded_total       — status == 'refunded'
          refunded_by_company  — refunded AND company involved (refunded_by IS NULL,
                                 'company' or 'both'); 'both' rows count in full here
                                 AND in refunded_by_bank, so the two can exceed
                                 refunded_total — subtract refunded_by_both for
                                 exclusive figures.
          refunded_by_bank     — refunded AND bank involved ('bank' or 'both')
          refunded_by_both     — refunded AND refunded_by == 'both' (the overlap)
        """
        from app.infrastructure.database.models.project import ProjectModel

        query = (
            self._session.query(
                InvoiceModel.refundable_status,
                InvoiceModel.refunded_by,
                InvoiceModel.items,
            )
            .join(ProjectModel, InvoiceModel.project_id == ProjectModel.id)
            .filter(
                InvoiceModel.type == InvoiceType.MATERIALS_SERVICES.value,
                ProjectModel.company_id.isnot(None),
                InvoiceModel.refundable_status.isnot(None),
            )
        )
        if not all_companies:
            query = query.filter(ProjectModel.company_id.in_(company_ids))

        refundable_amount = Decimal("0")
        refunded_total = Decimal("0")
        refunded_by_company = Decimal("0")
        refunded_by_bank = Decimal("0")
        refunded_by_both = Decimal("0")

        for status, refunded_by, items in query.all():
            row_total = _items_total(items)

            if status in (RefundableStatus.REFUNDABLE.value, RefundableStatus.REFUND_PENDING.value):
                refundable_amount += row_total
            elif status == RefundableStatus.REFUNDED.value:
                refunded_total += row_total
                # Involvement buckets: 'both' rows land in company AND bank
                # (per-source amounts are not tracked), plus the overlap bucket.
                if refunded_by in ("bank", "both"):
                    refunded_by_bank += row_total
                if refunded_by in (None, "company", "both"):
                    refunded_by_company += row_total
                if refunded_by == "both":
                    refunded_by_both += row_total

        return {
            "refundable_amount": float(refundable_amount),
            "refunded_total": float(refunded_total),
            "refunded_by_company": float(refunded_by_company),
            "refunded_by_bank": float(refunded_by_bank),
            "refunded_by_both": float(refunded_by_both),
        }

    def next_invoice_number(self, project_id: UUID) -> str:
        """Generate next sequential invoice number: PREFIX-YYYY-NNNN.

        Reads the project's custom invoice_prefix (falls back to "INV").
        """
        from app.infrastructure.database.models.project import ProjectModel

        project_row = self._session.query(ProjectModel.invoice_prefix).filter_by(id=project_id).first()
        tag = project_row[0] if project_row and project_row[0] else "INV"

        year = datetime.now(timezone.utc).year
        prefix = f"{tag}-{year}-"
        last = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.invoice_number.like(f"{prefix}%"),
            )
            .order_by(InvoiceModel.invoice_number.desc())
            .first()
        )
        n = 1
        if last:
            try:
                n = int(last.invoice_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{n:04d}"
