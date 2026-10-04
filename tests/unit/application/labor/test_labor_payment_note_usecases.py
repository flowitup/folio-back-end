"""Unit tests for labor payment note use-cases (in-memory fakes, no DB)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import pytest

from app.application.labor.labor_payment_note_usecases import (
    LaborPaymentNoteWorkerNotFound,
    ListLaborPaymentNotesRequest,
    ListLaborPaymentNotesUseCase,
    SetLaborPaymentNoteRequest,
    SetLaborPaymentNoteUseCase,
)
from app.application.labor.ports import ILaborPaymentNoteRepository
from app.domain.entities.labor_payment_note import LaborPaymentNote


class _InMemoryNoteRepo(ILaborPaymentNoteRepository):
    def __init__(self) -> None:
        self.store: Dict[tuple, LaborPaymentNote] = {}

    def find(self, project_id: UUID, worker_id: UUID, month: date) -> Optional[LaborPaymentNote]:
        return self.store.get((project_id, worker_id, month))

    def upsert(self, entity: LaborPaymentNote) -> LaborPaymentNote:
        self.store[(entity.project_id, entity.worker_id, entity.month)] = entity
        return entity

    def list_by_project(self, project_id: UUID, month: Optional[date] = None) -> List[LaborPaymentNote]:
        return [n for (pid, _w, m), n in self.store.items() if pid == project_id and (month is None or m == month)]

    def delete(self, project_id: UUID, worker_id: UUID, month: date) -> bool:
        return self.store.pop((project_id, worker_id, month), None) is not None


class _FakeWorker:
    def __init__(self, worker_id: UUID, project_id: UUID) -> None:
        self.id = worker_id
        self.project_id = project_id


class _FakeWorkerRepo:
    def __init__(self, *workers: _FakeWorker) -> None:
        self._by_id = {w.id: w for w in workers}

    def find_by_id(self, worker_id: UUID):
        return self._by_id.get(worker_id)


PROJECT = uuid4()
OTHER_PROJECT = uuid4()
WORKER = uuid4()
FOREIGN_WORKER = uuid4()


@pytest.fixture
def repo() -> _InMemoryNoteRepo:
    return _InMemoryNoteRepo()


@pytest.fixture
def set_uc(repo) -> SetLaborPaymentNoteUseCase:
    workers = _FakeWorkerRepo(_FakeWorker(WORKER, PROJECT), _FakeWorker(FOREIGN_WORKER, OTHER_PROJECT))
    return SetLaborPaymentNoteUseCase(repo, workers)


def _req(note: str, month: date = date(2026, 9, 1), worker_id: UUID = WORKER) -> SetLaborPaymentNoteRequest:
    return SetLaborPaymentNoteRequest(project_id=PROJECT, worker_id=worker_id, month=month, note=note)


def test_creates_note_trimmed_and_normalised_to_first_of_month(set_uc, repo):
    detail = set_uc.execute(_req("  rest paid in cash  ", month=date(2026, 9, 17)))

    assert detail is not None
    assert detail.note == "rest paid in cash"
    assert detail.month == "2026-09"
    assert list(repo.store) == [(PROJECT, WORKER, date(2026, 9, 1))]


def test_second_save_updates_same_row_and_keeps_created_at(set_uc, repo):
    first = set_uc.execute(_req("first"))
    second = set_uc.execute(_req("second"))

    assert first is not None and second is not None
    assert second.id == first.id
    assert second.note == "second"
    assert second.created_at == first.created_at
    assert len(repo.store) == 1


def test_blank_note_deletes_row_and_returns_none(set_uc, repo):
    set_uc.execute(_req("to clear"))

    assert set_uc.execute(_req("   ")) is None
    assert repo.store == {}


def test_blank_note_without_existing_row_is_a_no_op(set_uc, repo):
    assert set_uc.execute(_req("")) is None
    assert repo.store == {}


@pytest.mark.parametrize("worker_id", [FOREIGN_WORKER, uuid4()])
def test_rejects_worker_not_on_project(set_uc, repo, worker_id):
    with pytest.raises(LaborPaymentNoteWorkerNotFound):
        set_uc.execute(_req("note", worker_id=worker_id))
    assert repo.store == {}


def test_list_filters_by_month(set_uc, repo):
    set_uc.execute(_req("september", month=date(2026, 9, 1)))
    set_uc.execute(_req("august", month=date(2026, 8, 1)))
    list_uc = ListLaborPaymentNotesUseCase(repo)

    sept = list_uc.execute(ListLaborPaymentNotesRequest(project_id=PROJECT, month=date(2026, 9, 30)))
    every = list_uc.execute(ListLaborPaymentNotesRequest(project_id=PROJECT))

    assert [n.note for n in sept] == ["september"]
    assert [n.month for n in every] == ["2026-08", "2026-09"]


def test_entity_rejects_blank_note_and_mid_month_date():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        LaborPaymentNote(uuid4(), PROJECT, WORKER, date(2026, 9, 1), "  ", now, now)
    with pytest.raises(ValueError):
        LaborPaymentNote(uuid4(), PROJECT, WORKER, date(2026, 9, 2), "ok", now, now)
