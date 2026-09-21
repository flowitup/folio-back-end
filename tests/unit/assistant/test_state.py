"""Unit tests for `app.application.assistant.state` (S2 — feature C context building)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.assistant.models import Invoice, Line
from app.application.assistant.state import (
    WritableProject,
    amounts_sane,
    build_ticket_state,
    duplicate_candidates,
    match_candidates,
    recent_purchases,
    workers_on_site,
    writable_projects,
)
from app.domain.entities.invoice import Invoice as InvoiceEntity, InvoiceType
from app.domain.entities.project import Project
from app.domain.entities.worker import Worker
from app.domain.value_objects.invoice_item import InvoiceItem


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return list(self._projects)

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return next((p for p in self._projects if p.id == project_id), None)


class FakeAuthzReader:
    """Just enough of `AuthzReaderPort` for `has_permission("project:manage_invoices", ...)`."""

    def __init__(self, company_id: UUID, role: str = "manager", assigned: Optional[set[UUID]] = None) -> None:
        self._company_id = company_id
        self._role = role
        self._assigned = assigned or set()

    def company_role_for(self, user_id: UUID, company_id: UUID) -> Optional[str]:
        return self._role

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return project_id in self._assigned

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self._company_id

    def project_exists(self, project_id: UUID) -> bool:
        return True

    def primary_company_id(self, user_id: UUID) -> Optional[UUID]:
        return self._company_id

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        return []

    def company_roles_for(self, user_id: UUID) -> list[tuple[UUID, str]]:
        return [(self._company_id, self._role)]

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False

    def project_ids_for_company(self, company_id: UUID) -> list[UUID]:
        return list(self._assigned)

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: Optional[UUID]) -> list[tuple[str, str]]:
        return []


class FakeInvoiceRepo:
    def __init__(self, invoices: list[InvoiceEntity]) -> None:
        self._invoices = invoices

    def find_by_project_in_range(self, project_id, date_from, date_to, type_filter=None) -> list[InvoiceEntity]:
        rows = [
            inv for inv in self._invoices if inv.project_id == project_id and date_from <= inv.issue_date <= date_to
        ]
        if type_filter is not None:
            rows = [inv for inv in rows if inv.type == type_filter]
        return rows

    def find_by_id(self, invoice_id: UUID) -> Optional[InvoiceEntity]:
        return next((inv for inv in self._invoices if inv.id == invoice_id), None)


class FakeLaborEntryRepo:
    def __init__(self, entries: list) -> None:
        self._entries = entries

    def list_by_project(self, project_id, date_from=None, date_to=None, worker_id=None, limit=None, status=None):
        return [
            e
            for e in self._entries
            if e.project_id == project_id
            and (date_from is None or e.date >= date_from)
            and (date_to is None or e.date <= date_to)
        ]


class FakeWorkerRepo:
    def __init__(self, workers: dict[UUID, Worker]) -> None:
        self._workers = workers

    def find_by_id(self, worker_id: UUID) -> Optional[Worker]:
        return self._workers.get(worker_id)

    def list_by_project(self, project_id: UUID, active_only: bool = True) -> list[Worker]:
        return [w for w in self._workers.values() if w.project_id == project_id]


class _FakeEntry:
    def __init__(self, project_id: UUID, worker_id: UUID, day: date) -> None:
        self.project_id = project_id
        self.worker_id = worker_id
        self.date = day


class FakeImportRepo:
    def __init__(self, invoice_ids_with_scan: set[UUID]) -> None:
        self._with_scan = invoice_ids_with_scan

    def list_without_scan_for_invoices(self, invoice_ids: list[UUID]) -> list[UUID]:
        return [invoice_id for invoice_id in invoice_ids if invoice_id not in self._with_scan]


def _project(name: str = "Villa Arcueil") -> Project:
    return Project(
        id=uuid4(), name=name, owner_id=uuid4(), created_at=datetime.now(timezone.utc), address="12 rue X, Paris"
    )


def _invoice_entity(project_id: UUID, merchant: str, total: str, day: date) -> InvoiceEntity:
    now = datetime.now(timezone.utc)
    return InvoiceEntity(
        id=uuid4(),
        project_id=project_id,
        invoice_number="FA-2026-0001",
        type=InvoiceType.MATERIALS_SERVICES,
        issue_date=day,
        recipient_name=merchant,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
        items=[
            InvoiceItem(description="Ciment", quantity=Decimal("1"), unit_price=Decimal(total), vat_rate=Decimal("0"))
        ],
    )


def _assistant_invoice(merchant: str = "Leroy Merlin", total_ttc: float = 100.0, day: str = "2026-09-10") -> Invoice:
    return Invoice(merchant=merchant, date=day, total_ttc=total_ttc, readability=0.9)


class TestWritableProjects:
    def test_only_projects_granting_manage_invoices_are_returned(self) -> None:
        company_id = uuid4()
        granted = _project("Chantier A")
        ungranted = _project("Chantier B")
        repo = FakeProjectRepo([granted, ungranted])
        authz = FakeAuthzReader(company_id, role="manager", assigned={granted.id})
        result = writable_projects(repo, authz, uuid4(), [company_id])
        assert [p.name for p in result] == ["Chantier A"]
        assert result[0].address == "12 rue X, Paris"


class TestWorkersOnSite:
    def test_lists_worker_names_for_the_day(self) -> None:
        project = _project()
        worker_id = uuid4()
        worker = Worker(
            id=worker_id,
            project_id=project.id,
            name="Jean",
            daily_rate=Decimal("150"),
            created_at=datetime.now(timezone.utc),
        )
        entries = [_FakeEntry(project.id, worker_id, date(2026, 9, 10))]
        names = workers_on_site(
            FakeLaborEntryRepo(entries), FakeWorkerRepo({worker_id: worker}), project.id, date(2026, 9, 10)
        )
        assert names == ["Jean"]

    def test_empty_when_no_entries_that_day(self) -> None:
        project = _project()
        names = workers_on_site(FakeLaborEntryRepo([]), FakeWorkerRepo({}), project.id, date(2026, 9, 10))
        assert names == []


class TestRecentPurchases:
    def test_matches_same_merchant_within_90_days(self) -> None:
        project = _project()
        invoice = _invoice_entity(project.id, "Leroy Merlin Ivry", "42.00", date(2026, 9, 1))
        hits = recent_purchases(FakeInvoiceRepo([invoice]), project.id, "Leroy Merlin", date(2026, 9, 15))
        assert len(hits) == 1
        assert hits[0]["total_ttc"] == 42.0


class TestDuplicateCandidates:
    def test_flags_close_amount_same_merchant_within_30_days(self) -> None:
        project = _project()
        invoice = _invoice_entity(project.id, "Point P", "79.50", date(2026, 9, 8))
        candidates = duplicate_candidates(
            FakeInvoiceRepo([invoice]),
            [WritableProject(project.id, project.name, project.address)],
            "Point P",
            79.54,
            date(2026, 9, 10),
        )
        assert len(candidates) == 1
        assert candidates[0].invoice_id == invoice.id

    def test_no_match_when_amount_too_different(self) -> None:
        project = _project()
        invoice = _invoice_entity(project.id, "Point P", "10.00", date(2026, 9, 8))
        candidates = duplicate_candidates(
            FakeInvoiceRepo([invoice]),
            [WritableProject(project.id, project.name, project.address)],
            "Point P",
            79.54,
            date(2026, 9, 10),
        )
        assert candidates == []


class TestMatchCandidates:
    def test_excludes_invoices_that_already_have_a_scan(self) -> None:
        project = _project()
        invoice = _invoice_entity(project.id, "Leroy Merlin", "79.54", date(2026, 9, 10))
        writable = [WritableProject(project.id, project.name, project.address)]
        ticket = _assistant_invoice(merchant="Leroy Merlin", total_ttc=79.54, day="2026-09-10")

        no_scan_yet = match_candidates(
            FakeInvoiceRepo([invoice]), FakeImportRepo(set()), writable, ticket, date(2026, 9, 10)
        )
        assert len(no_scan_yet) == 1

        already_scanned = match_candidates(
            FakeInvoiceRepo([invoice]), FakeImportRepo({invoice.id}), writable, ticket, date(2026, 9, 10)
        )
        assert already_scanned == []


class TestAmountsSane:
    def test_true_when_ht_plus_tva_equals_ttc(self) -> None:
        invoice = Invoice(merchant="X", total_ht=83.28, total_tva=16.66, total_ttc=99.94, readability=0.9)
        assert amounts_sane(invoice) is True

    def test_false_when_ht_plus_tva_diverges(self) -> None:
        invoice = Invoice(merchant="X", total_ht=10.0, total_tva=1.0, total_ttc=50.0, readability=0.9)
        assert amounts_sane(invoice) is False

    def test_false_when_lines_sum_diverges_from_total(self) -> None:
        invoice = Invoice(
            merchant="X",
            total_ttc=100.0,
            readability=0.9,
            lines=[Line(label="A", total_ttc=10.0), Line(label="B", total_ttc=10.0)],
        )
        assert amounts_sane(invoice) is False


class TestBuildTicketState:
    def test_shape(self) -> None:
        project = _project()
        writable = [WritableProject(project.id, project.name, project.address)]
        ticket = _assistant_invoice()
        state = build_ticket_state(ticket, writable, {str(project.id): ["Jean"]}, {str(project.id): []}, [], True)
        assert state["invoice"]["merchant"] == "Leroy Merlin"
        assert state["projects"][0]["name"] == project.name
        assert state["workers_on_site_that_day"][str(project.id)] == ["Jean"]
        assert state["amounts_sane"] is True
