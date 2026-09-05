"""Invoice repository port — persistence contract for the invoice domain."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import BinaryIO, List, Optional, Protocol
from uuid import UUID

from app.domain.entities.invoice import Invoice, InvoiceType


@dataclass(frozen=True)
class WorkerRef:
    """Minimal worker projection the invoice BC needs to validate/snapshot a worker link.

    display_name is the resolved COALESCE(persons.name, workers.name) — the same
    display precedence used by labor summaries — snapshotted into
    Invoice.recipient_name when a worker is linked.
    """

    id: UUID
    project_id: UUID
    display_name: str


class WorkerReaderPort(Protocol):
    """Cross-BC port: invoice → labor, read-only worker lookup for the worker_id link.

    Keeps the invoice BC decoupled from labor's WorkerModel / IWorkerRepository —
    only the minimal projection needed to validate a worker link and snapshot its
    display name is exposed here.
    """

    def get_for_project(self, worker_id: UUID, project_id: UUID) -> Optional[WorkerRef]:
        """Return the worker if it exists AND belongs to project_id, else None.

        A worker that exists but belongs to a different project must return
        None — same as a worker that does not exist at all — so callers never
        leak cross-project existence.
        """
        ...


@dataclass(frozen=True)
class LaborPaymentsWorkerRow:
    """One worker's paid total within a labor-payments month bucket."""

    worker_id: UUID
    worker_name: str
    paid: Decimal
    invoice_count: int


@dataclass(frozen=True)
class LaborPaymentsMonthRow:
    """One (service_month) bucket of the labor-payments summary.

    ``year``/``month`` are both None for the no-service_month bucket (labor
    invoices with a NULL service_month), which the repository always places
    last regardless of the other buckets' chronological order.

    ``unassigned_paid``/``unassigned_count`` roll up rows whose ``worker_id``
    is NULL — either the invoice never recorded a worker, or the linked
    worker was deleted (ON DELETE SET NULL).

    ``company_paid``/``personal_paid`` split the bucket's whole paid amount
    (worker-linked and unassigned alike) by the invoice's payment-method flag
    (``is_company_payment`` / ``is_personal_payment``). Invoices with no
    method or an unflagged method belong to neither figure, so
    company_paid + personal_paid <= the bucket total — same flagged-only
    semantics as the expense-page spent buckets.
    """

    year: Optional[int]
    month: Optional[int]
    workers: List[LaborPaymentsWorkerRow] = field(default_factory=list)
    unassigned_paid: Decimal = Decimal("0")
    unassigned_count: int = 0
    company_paid: Decimal = Decimal("0")
    personal_paid: Decimal = Decimal("0")


class BankRefundReleasePort(Protocol):
    """Cross-BC port: invoice → billing/funds-release, bank-refund lifecycle only.

    Split out of billing.ports.FundsReleasePort: that port's sibling method
    create_funds_release deliberately takes primitives to keep the billing BC
    decoupled from the invoice BC, but these two methods take the whole
    Invoice entity — Invoice already lives in this BC, and
    SetInvoiceRefundableStatusUseCase (this BC) is their only consumer, so they
    are declared here instead of in billing.ports. FundsReleaseAdapter
    satisfies this Protocol structurally, with no changes needed on the
    adapter itself.
    """

    def create_bank_refund_release(self, source: Invoice, created_by: UUID) -> None:
        """Create (idempotently) the auto-generated released_funds release for a
        bank-refunded materials_services expense.

        No-op when source.type is not materials_services, when
        source.total_amount <= 0, or when a bank-refund release already exists
        for source.id.
        """
        ...

    def delete_bank_refund_release(self, source_id: UUID) -> None:
        """Delete the auto-generated bank-refund release linked to source_id, if any."""
        ...


class IInvoiceRepository(ABC):
    """Port defining the invoice persistence contract."""

    @abstractmethod
    def create(self, invoice: Invoice) -> Invoice: ...

    @abstractmethod
    def find_by_id(self, invoice_id: UUID) -> Optional[Invoice]: ...

    @abstractmethod
    def list_by_project(
        self,
        project_id: UUID,
        invoice_type: Optional[InvoiceType] = None,
        tag_id: Optional[UUID] = None,
        service_month: Optional[date] = None,
        worker_id: Optional[UUID] = None,
    ) -> list[Invoice]:
        """List a project's invoices, optionally filtered by type/tag/service_month/worker_id.

        service_month matches labor rows whose service_month equals the given
        (already first-of-month-normalized) date exactly. worker_id matches
        the invoice's linked worker exactly. Filters compose (AND).
        """
        ...

    @abstractmethod
    def update(self, invoice: Invoice) -> Invoice: ...

    @abstractmethod
    def delete(self, invoice_id: UUID) -> bool: ...

    @abstractmethod
    def next_invoice_number(self, project_id: UUID) -> str: ...

    @abstractmethod
    def find_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
        type_filter: Optional[InvoiceType] = None,
    ) -> list[Invoice]:
        """Return invoices for the project where issue_date ∈ [date_from, date_to],
        optionally filtered by type. Returns [] if none."""
        ...

    @abstractmethod
    def sum_funds_released(self, project_id: UUID) -> Decimal:
        """Sum total_amount for all released_funds invoices in a project."""
        ...

    @abstractmethod
    def sum_funds_released_split(self, project_id: UUID) -> tuple[Decimal, Decimal, Decimal]:
        """Split released_funds rows into (company_total, personal_total, cash_advanced_total).

        personal_total: released_funds invoices whose payment_method_id belongs to a
        method flagged is_personal_payment.
        company_total: every other released_funds invoice (company-flagged, unflagged,
        or NULL payment_method_id).
        cash_advanced_total: released_funds invoices flagged is_cash_advance (company
        money handed to a person). Excluded from the two totals above — a handover is
        an internal transfer, not a release into the project.

        Invariant: company_total + personal_total == sum_funds_released(project_id).
        """
        ...

    @abstractmethod
    def sum_personal_spent(self, project_id: UUID) -> Decimal:
        """Sum amounts spent personally (out-of-pocket, non-company) on a project.

        Counts non-released_funds invoices paid via a method flagged
        is_personal_payment, EXCLUDING rows where the company already reimbursed
        the expense (refundable_status == 'refunded' AND refunded_by != 'bank';
        NULL refunded_by counts as company-refunded). Bank-refunded rows still
        count. Refund-type invoices net the total down (mirrors sum_company_spent).
        Result is floored at 0.
        """
        ...

    @abstractmethod
    def sum_spent_split(self, project_id: UUID) -> tuple[Decimal, Decimal]:
        """Return (company_spent_total, personal_spent_total) computed in one scan.

        Equivalent to calling sum_company_spent and sum_personal_spent separately,
        but scans the project's non-released_funds invoices once instead of twice.
        Each bucket keeps its own independent rules and floor-at-0 exactly as
        sum_company_spent / sum_personal_spent define them.
        """
        ...

    @abstractmethod
    def find_bank_refund_release(self, source_id: UUID) -> Optional[Invoice]:
        """Return the auto-generated bank-refund release linked to source_id, or None.

        Matches only rows where type == 'released_funds' AND
        refunds_invoice_id == source_id AND is_auto_generated is True. Facture-driven
        releases (source_billing_document_id set, refunds_invoice_id always NULL on
        those rows) can never match this predicate.
        """
        ...

    @abstractmethod
    def delete_bank_refund_release(self, source_id: UUID) -> None:
        """Delete the auto-generated bank-refund release linked to source_id, if any.

        Matches only rows where type == 'released_funds' AND
        refunds_invoice_id == source_id AND is_auto_generated is True. No-op when
        none exists. Never touches facture-driven releases — those rows always have
        refunds_invoice_id IS NULL, so they can never match this predicate.
        """
        ...

    @abstractmethod
    def sum_company_spent(self, project_id: UUID) -> Decimal:
        """Sum amounts the company spent directly on a project.

        Counts non-released_funds invoices where refundable_status == 'refunded'
        OR payment_method_id is a company-flagged method.  Soft-deleted flagged
        methods still count.
        """
        ...

    @abstractmethod
    def sum_refunds_for_source(self, source_id: UUID, exclude_invoice_id: "UUID | None" = None) -> Decimal:
        """Sum total_amount of all refund invoices linked to source_id.

        Only counts invoices of type 'refund' with refunds_invoice_id == source_id.
        When exclude_invoice_id is provided, that invoice's own row is excluded
        from the sum (used on update to avoid self-double-counting).
        Returns Decimal("0") when no matching rows exist.
        """
        ...

    @abstractmethod
    def sum_applied_for_target(self, target_id: UUID, exclude_invoice_id: "UUID | None" = None) -> Decimal:
        """Sum total_amount of all avoir return invoices applied to target_id.

        Only counts invoices of type 'return' with applied_to_invoice_id == target_id.
        When exclude_invoice_id is provided, that invoice's own row is excluded
        from the sum (used on update to avoid self-double-counting).
        Returns Decimal("0") when no matching rows exist.
        """
        ...

    @abstractmethod
    def applied_return_summaries(self, invoice_ids: list[UUID]) -> dict[UUID, list[dict]]:
        """Return {target_invoice_id: [{"invoice_number": str, "total_amount": float}, ...]}.

        Reverse lookup powering 'paid_with_returns': for each id in invoice_ids, the
        avoir return invoices whose applied_to_invoice_id points at it.

        Batch reverse-lookup — one query regardless of input size. Empty input
        returns an empty dict without issuing a query.
        """
        ...

    @abstractmethod
    def refund_source_ids(self, source_ids: list[UUID]) -> set[UUID]:
        """Return the subset of source_ids that have ≥1 linked CASH refund invoice.

        A source qualifies when at least one invoice exists with type 'return',
        refunds_invoice_id == source_id, and settled_via is not 'avoir'. Used to
        flag "refunded by bank" (a supplier/vendor sent money back) on
        materials_services expenses.

        Avoir-settled returns do NOT qualify: an avoir is a credit note applied
        to another invoice, so no money came back. Legacy returns predate
        settled_via and carry NULL — those count as cash.

        Batch reverse-lookup — one query regardless of input size. Empty input
        returns an empty set without issuing a query.
        """
        ...

    @abstractmethod
    def bank_refund_release_numbers(self, source_ids: list[UUID]) -> dict[UUID, str]:
        """Return {source_invoice_id: released_funds invoice_number} for bank-refund releases.

        A source_id qualifies when an invoice exists with type 'released_funds',
        is_auto_generated is True, and refunds_invoice_id == source_id. Used to
        surface the FR-YYYY-NNNN release number tied to a bank-refunded
        materials_services expense.

        Batch reverse-lookup — one query regardless of input size. Empty input
        returns an empty dict without issuing a query.
        """
        ...

    @abstractmethod
    def list_materials_services_by_companies(
        self,
        company_ids: list[UUID],
        refundable: Optional[bool],
        limit: int,
        offset: int,
        all_companies: bool = False,
    ) -> tuple[list[dict], int]:
        """Return paginated materials_services invoices across projects of company_ids.

        Each row dict includes all Invoice fields plus:
          - 'project_name' (string): resolved via JOIN, no N+1.
          - 'attachments' (list[dict]): each dict has id, filename, mime_type, size_bytes.
            Loaded in a single batch query (one IN clause) over the page's invoice ids.
            Empty list when no attachments exist for that invoice.

        all_companies=True skips the company_ids filter (superadmin cross-company view).
        refundable=True  → only rows where refundable_status IS NOT NULL
        refundable=False → only rows where refundable_status IS NULL
        refundable=None  → no status filter

        Ordered by issue_date DESC, created_at DESC.
        Returns (rows, total_count).
        """
        ...

    @abstractmethod
    def materials_services_refund_summary(
        self,
        company_ids: list[UUID],
        all_companies: bool = False,
    ) -> dict:
        """Aggregate refund totals over the FULL materials_services filter set (not paginated).

        Same scope filters as list_materials_services_by_companies with refundable=True
        (type=materials_services, ProjectModel.company_id.isnot(None), company scope,
        refundable_status.isnot(None)).

        Returns a dict of floats:
          refundable_amount    — sum of totals where status in ('refundable', 'refund_pending')
          refunded_total       — sum of totals where status == 'refunded'
          refunded_by_company  — refunded_total subset where refunded_by is NULL or 'company'
          refunded_by_bank     — refunded_total subset where refunded_by == 'bank'
        """
        ...

    @abstractmethod
    def get_labor_payments_summary(self, project_id: UUID) -> List[LaborPaymentsMonthRow]:
        """Aggregate paid amounts per (service_month, worker) for the project's labor invoices.

        Scoped to type == 'labor' only. Buckets by service_month (None groups
        into the no-month bucket) then by worker_id within each bucket; rows
        with a NULL worker_id (never linked, or the linked worker was
        deleted via ON DELETE SET NULL) roll into that bucket's
        unassigned_paid/unassigned_count instead of the workers list.

        Returned buckets are ordered most-recent service_month first, with
        the no-month bucket (year=None, month=None) always last. Returns []
        for a project with no labor invoices.
        """
        ...


class IAttachmentStorage(ABC):
    """Port for binary file storage (S3 / MinIO / local FS)."""

    @abstractmethod
    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        """Upload a file. `key` is the storage object path."""

    @abstractmethod
    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        """Open a download stream. Returns (file-like, content_length)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove an object. Idempotent — no-op if key does not exist."""


class IInvoiceAttachmentRepository(ABC):
    """Port for invoice attachment metadata persistence."""

    @abstractmethod
    def save(self, attachment) -> "InvoiceAttachment":  # noqa: F821 — fwd ref
        ...

    @abstractmethod
    def find_by_id(self, attachment_id: UUID): ...

    @abstractmethod
    def list_by_invoice(self, invoice_id: UUID) -> list: ...

    @abstractmethod
    def update_filename(self, attachment_id: UUID, new_filename: str) -> bool: ...

    @abstractmethod
    def delete(self, attachment_id: UUID) -> bool: ...
