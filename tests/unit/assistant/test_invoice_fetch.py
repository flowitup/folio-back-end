"""Unit tests for `app.application.assistant.features.invoice_fetch` (feature B).

Uses REAL `SQLAlchemyInvoiceRepository`/`SqlAlchemyAssistantJobRepository`/
`CreateInvoiceUseCase`/`UploadAttachmentUseCase`/`TicketFeature` against the in-memory
SQLite DB (the `session` fixture) — same pattern as `test_ticket.py` — so "the done path
creates a real invoice through the ticket pipeline" and "the job row's state machine
transitions are correct" are proven against real persistence, not a canned fake.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

import cv2
import numpy as np
import shutil

import pytest

from app.application.assistant.features.invoice_fetch import (
    InvoiceFetchFeature,
    _normalize_amount,
    _normalize_date,
)
from app.application.assistant.features.ticket import TicketFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import AmountDate, ChannelScope, RouterDecision
from tests.fakes.ai import RecordingImageGen, ScriptedDecision, ScriptedVision
from app.application.invoice.create_invoice import CreateInvoiceUseCase
from app.application.invoice.delete_invoice import DeleteInvoiceUseCase
from app.application.invoice.upload_attachment import UploadAttachmentUseCase
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.domain.entities.project import Project
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.adapters.sqlalchemy_invoice_attachment import SQLAlchemyInvoiceAttachmentRepository
from app.infrastructure.database.repositories.sqlalchemy_assistant_import_repository import (
    SqlAlchemyAssistantImportRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_assistant_job_repository import (
    SqlAlchemyAssistantJobRepository,
)

# ---------------------------------------------------------------------------
# Pure post-processing normalisation table
# ---------------------------------------------------------------------------

_TODAY = date(2026, 9, 21)


class TestNormalizeAmount:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (79.54, 79.54),
            ("79.54", 79.54),
            ("79,54", 79.54),
            ("79,54e", 79.54),
            ("79,54€", 79.54),
            ("  12,00 EUR ", 12.0),
            (None, None),
            ("", None),
            ("abc", None),
        ],
    )
    def test_table(self, raw: Any, expected: Optional[float]) -> None:
        assert _normalize_amount(raw) == expected


class TestNormalizeDate:
    def test_full_iso_date_kept(self) -> None:
        assert _normalize_date("2026-09-10", _TODAY) == date(2026, 9, 10)

    def test_missing_year_gets_current_year(self) -> None:
        assert _normalize_date("09-10", _TODAY) == date(2026, 9, 10)

    def test_future_date_rolls_back_one_year(self) -> None:
        # "12-25" with today=2026-09-21 would be in the future this year -> last year.
        assert _normalize_date("12-25", _TODAY) == date(2025, 12, 25)

    def test_iso_date_in_the_future_also_rolls_back(self) -> None:
        assert _normalize_date("2026-12-25", _TODAY) == date(2025, 12, 25)

    def test_none_and_garbage(self) -> None:
        assert _normalize_date(None, _TODAY) is None
        assert _normalize_date("hier", _TODAY) is None


# ---------------------------------------------------------------------------
# Fakes shared with test_ticket.py's style (kept local — small, test-file scoped)
# ---------------------------------------------------------------------------


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._by_id = {p.id: p for p in projects}

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return list(self._by_id.values())

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return self._by_id.get(project_id)


class FakeAuthzReader:
    """``project_company_id`` defaults to the constructor's ``company_id`` for every
    project (every project in this file's ``World`` belongs to ``World.company_id`` by
    default), with per-project overrides for a NEW-H1 test that needs a project rowed
    to a different, foreign company — mirrors ``test_ticket.py``'s fake."""

    def __init__(self, company_id: Optional[UUID] = None, project_companies: Optional[dict[UUID, UUID]] = None) -> None:
        self._company_id = company_id or uuid4()
        self._project_companies = project_companies or {}

    def company_role_for(self, user_id: UUID, company_id: UUID) -> Optional[str]:
        return "manager"

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self._project_companies.get(project_id, self._company_id)

    def project_exists(self, project_id: UUID) -> bool:
        return True

    def primary_company_id(self, user_id: UUID) -> Optional[UUID]:
        return None

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        return []

    def company_roles_for(self, user_id: UUID) -> list[tuple[UUID, str]]:
        return []

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False

    def project_ids_for_company(self, company_id: UUID) -> list[UUID]:
        return []

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: Optional[UUID]) -> list[tuple[str, str]]:
        return []


@dataclass
class _Access:
    company_id: UUID


class FakeCompanyAccessRepo:
    def __init__(self, *company_ids: UUID) -> None:
        self._company_ids = list(company_ids)

    def list_for_user(self, user_id: UUID) -> list[_Access]:
        return [_Access(company_id=cid) for cid in self._company_ids]


class FakeLaborEntryRepo:
    def list_by_project(self, project_id, date_from=None, date_to=None, worker_id=None, limit=None, status=None):
        return []


class FakeWorkerRepo:
    def find_by_id(self, worker_id: UUID):
        return None


class FakeMessageRepo:
    def __init__(self) -> None:
        self.messages: dict[UUID, ChatMessage] = {}

    def add(self, message: ChatMessage) -> None:
        self.messages[message.id] = message

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        return self.messages.get(message_id)

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None:
        message = self.messages[message_id]
        self.messages[message_id] = ChatMessage(
            id=message.id,
            channel=message.channel,
            sender_id=message.sender_id,
            body=message.body,
            attachment=message.attachment,
            created_at=message.created_at,
            sender_type=message.sender_type,
            content_type=message.content_type,
            payload=payload,
            reply_to_id=message.reply_to_id,
            ai_trace_id=message.ai_trace_id,
        )

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        return []


class FakeSession:
    def commit(self) -> None:
        pass


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Optional[str]]] = []

    def message_sent_by_assistant(self, *, channel: Any, preview: Optional[str], sent_at: datetime) -> None:
        self.calls.append((channel, preview))


def _photo_bytes() -> bytes:
    canvas = np.zeros((400, 300, 3), dtype=np.uint8)
    cv2.rectangle(canvas, (40, 40), (260, 360), (255, 255, 255), -1)
    ok, buffer = cv2.imencode(".jpg", canvas)
    assert ok
    return buffer.tobytes()


def _pdf_bytes() -> bytes:
    import img2pdf

    result: bytes = img2pdf.convert(_photo_bytes())
    return result


def _project(name: str = "Villa Arcueil") -> Project:
    return Project(id=uuid4(), name=name, owner_id=uuid4(), created_at=datetime.now(timezone.utc), address="12 rue X")


# ---------------------------------------------------------------------------
# World fixture
# ---------------------------------------------------------------------------


class World:
    def __init__(self, session) -> None:
        self.session = session
        self.user_id = uuid4()
        self.company_id = uuid4()
        self.project_a = _project("Villa Arcueil")
        self.project_repo = FakeProjectRepo([self.project_a])
        self.authz_reader = FakeAuthzReader(self.company_id)
        self.company_access = FakeCompanyAccessRepo(self.company_id)
        self.labor_entry_repo = FakeLaborEntryRepo()
        self.worker_repo = FakeWorkerRepo()
        self.invoice_repo = SQLAlchemyInvoiceRepository(session)
        self.attachment_repo = SQLAlchemyInvoiceAttachmentRepository(session)
        self.import_repo = SqlAlchemyAssistantImportRepository(session)
        self.job_repo = SqlAlchemyAssistantJobRepository(session)
        self.storage = InMemoryDocumentStorage()
        self.messages = FakeMessageRepo()
        self.notifier = RecordingNotifier()
        self.messenger = AssistantMessenger(self.messages, FakeSession())
        self.messenger.notifier = self.notifier
        self.create_invoice_usecase = CreateInvoiceUseCase(self.invoice_repo)
        self.delete_invoice_usecase = DeleteInvoiceUseCase(self.invoice_repo, self.attachment_repo, self.storage)
        self.upload_attachment_usecase = UploadAttachmentUseCase(self.invoice_repo, self.attachment_repo, self.storage)
        self.vision = ScriptedVision(json_answers=[])
        self.decisions = ScriptedDecision()
        self.image_gen = RecordingImageGen()
        self.ticket = TicketFeature(
            vision=self.vision,
            decisions=self.decisions,
            image_gen=self.image_gen,
            scan_mode="opencv",
            messages=self.messages,
            storage=self.storage,
            company_access=self.company_access,
            project_repo=self.project_repo,
            authz_reader=self.authz_reader,
            invoice_repo=self.invoice_repo,
            attachment_repo=self.attachment_repo,
            worker_repo=self.worker_repo,
            labor_entry_repo=self.labor_entry_repo,
            import_repo=self.import_repo,
            create_invoice_usecase=self.create_invoice_usecase,
            delete_invoice_usecase=self.delete_invoice_usecase,
            upload_attachment_usecase=self.upload_attachment_usecase,
        )
        self.feature = InvoiceFetchFeature(
            vision=self.vision,
            messages=self.messages,
            storage=self.storage,
            job_repo=self.job_repo,
            ticket=self.ticket,
            company_access=self.company_access,
            project_repo=self.project_repo,
            authz_reader=self.authz_reader,
            invoice_repo=self.invoice_repo,
        )

    def post_user_message(self, text: str, lang: str = "fr") -> ChatMessage:
        message = ChatMessage.create(
            channel=ChannelRef(kind="assistant", id=self.user_id),
            sender_id=self.user_id,
            body=text,
            attachment=None,
        )
        message = ChatMessage(
            id=message.id,
            channel=message.channel,
            sender_id=message.sender_id,
            body=message.body,
            attachment=message.attachment,
            created_at=message.created_at,
            sender_type=message.sender_type,
            content_type=message.content_type,
            payload={"lang": lang},
            reply_to_id=message.reply_to_id,
            ai_trace_id=message.ai_trace_id,
        )
        self.messages.add(message)
        return message

    def default_scope(self) -> ChannelScope:
        """A generic company-channel scope — every real ``fetch_invoice``/``handle_action``
        call now requires one; most tests here don't care which channel, only that
        ``on_result`` has a real ``channel_key`` to resolve back (M1: a NULL/unparsable
        one now means the reply is dropped, matching production's post-migration
        behaviour where a job is never created without one)."""
        return ChannelScope(
            kind="company", company_id=self.company_id, project_id=None, is_admin_channel=False, asker_id=self.user_id
        )

    _NO_CHANNEL_KEY_GIVEN = "__use_default_scope__"

    def create_job(
        self,
        *,
        merchant: str = "leroymerlin",
        amount: Decimal = Decimal("79.54"),
        job_date: date = date(2026, 9, 10),
        reply_to: Optional[ChatMessage] = None,
        channel_key: Optional[str] = _NO_CHANNEL_KEY_GIVEN,
    ):
        # `channel_key=None` (as opposed to simply omitted) is a deliberate M1 test of
        # the pre-migration "no channel at all" case, so it must NOT fall back to the
        # default scope's channel — only an omitted argument does.
        resolved_channel_key = (
            self.default_scope().channel.key if channel_key == self._NO_CHANNEL_KEY_GIVEN else channel_key
        )
        job = self.job_repo.add(
            user_id=self.user_id,
            merchant=merchant,
            amount_ttc=amount,
            date=job_date,
            project_hint=None,
            channel_key=resolved_channel_key,
        )
        status_message = self.messenger.post_job_status(
            self.user_id,
            job_id=str(job.id),
            state="queued",
            text="Je m'en occupe",
            reply_to_id=reply_to.id if reply_to is not None else None,
            scope=None,
        )
        self.job_repo.set_status_message(job.id, status_message.id)
        return self.job_repo.find_by_id(job.id)


# ---------------------------------------------------------------------------
# fetch_invoice() — job creation
# ---------------------------------------------------------------------------


class TestFetchInvoice:
    def test_creates_job_and_acks(self, session) -> None:
        world = World(session)
        message = world.post_user_message("va chercher la facture Leroy Merlin 79,54 e d'hier")
        world.vision._json_answers = [AmountDate(amount_ttc=79.54, date="2026-09-20")]
        decision = RouterDecision(intent="fetch_invoice", intent_confidence=0.95, merchant="leroymerlin")

        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            decision=decision,
            scope=world.default_scope(),
        )

        jobs = world.job_repo.list_recent_for_user(world.user_id)
        assert len(jobs) == 1
        assert jobs[0].merchant == "leroymerlin"
        assert jobs[0].amount_ttc == Decimal("79.54")
        assert jobs[0].status == "queued"
        assert jobs[0].status_message_id is not None
        status_message = world.messages.find_by_id(jobs[0].status_message_id)
        assert status_message is not None
        assert status_message.content_type == "job_status"

    def test_missing_merchant_asks(self, session) -> None:
        world = World(session)
        message = world.post_user_message("va chercher ma facture de 50 euros")
        decision = RouterDecision(intent="fetch_invoice", intent_confidence=0.9, merchant="none")

        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            decision=decision,
            scope=world.default_scope(),
        )

        assert world.job_repo.list_recent_for_user(world.user_id) == []
        replies = [m for m in world.messages.messages.values() if m.reply_to_id == message.id]
        assert len(replies) == 1
        assert "fournisseur" in (replies[0].body or "")

    def test_missing_amount_asks(self, session) -> None:
        world = World(session)
        message = world.post_user_message("va chercher ma facture Leroy Merlin")
        world.vision._json_answers = [AmountDate(amount_ttc=None, date=None)]
        decision = RouterDecision(intent="fetch_invoice", intent_confidence=0.9, merchant="leroymerlin")

        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            decision=decision,
            scope=world.default_scope(),
        )

        assert world.job_repo.list_recent_for_user(world.user_id) == []

    def test_dedupe_within_24h_reuses_active_job(self, session) -> None:
        world = World(session)
        message = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        world.vision._json_answers = [AmountDate(amount_ttc=79.54, date="2026-09-10")]
        decision = RouterDecision(intent="fetch_invoice", intent_confidence=0.9, merchant="leroymerlin")

        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            decision=decision,
            scope=world.default_scope(),
        )
        assert len(world.job_repo.list_recent_for_user(world.user_id)) == 1

        message2 = world.post_user_message("et celle de Leroy Merlin 79,54e aussi")
        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message2.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t2",
            decision=decision,
            scope=world.default_scope(),
        )
        # No second job created — the existing active one is reused.
        assert len(world.job_repo.list_recent_for_user(world.user_id)) == 1
        replies = [m for m in world.messages.messages.values() if m.reply_to_id == message2.id]
        assert len(replies) == 1


# ---------------------------------------------------------------------------
# on_result() — the job's state machine
# ---------------------------------------------------------------------------


class TestOnResultNotReady:
    def test_requeues_with_backoff_below_max_attempts(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(reply_to=original)
        world.job_repo.update_result(job.id, status="not_ready", result={"status": "not_ready", "message": "wait"})
        world.notifier.calls.clear()  # drop the push from create_job()'s own job_status post

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        updated = world.job_repo.find_by_id(job.id)
        assert updated.status == "queued"
        assert updated.attempts == 1
        run_after = updated.run_after
        if run_after.tzinfo is None:  # SQLite drops tzinfo on round-trip
            run_after = run_after.replace(tzinfo=timezone.utc)
        assert run_after > datetime.now(timezone.utc) + timedelta(minutes=1)
        assert world.notifier.calls == []  # non-terminal: no push

    def test_fails_after_max_attempts(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(reply_to=original)
        world.job_repo.update_status(job.id, status="queued", attempts=2)
        world.job_repo.update_result(job.id, status="not_ready", result={"status": "not_ready"})
        world.notifier.calls.clear()

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        updated = world.job_repo.find_by_id(job.id)
        assert updated.status == "failed"
        assert len(world.notifier.calls) == 1  # terminal: pushed once


class TestNullChannelKeyDropsTheReply:
    """M1: a job queued before `channel_key` existed (or with an unparsable one, e.g.
    the retired `assistant:` kind) has nowhere safe to post — the reply is dropped and
    the job is still marked processed."""

    def test_null_channel_key_drops_the_reply_and_marks_processed(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(reply_to=original, channel_key=None)
        world.job_repo.update_result(job.id, status="blocked", result={"status": "blocked"})
        world.notifier.calls.clear()

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        assert world.messages.messages[job.status_message_id].payload["state"] == "queued"  # never updated
        assert world.notifier.calls == []
        updated = world.job_repo.find_by_id(job.id)
        assert updated.processed_at is not None


class TestOnResultBlocked:
    def test_marks_blocked_and_pushes(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(reply_to=original)
        world.job_repo.update_result(job.id, status="blocked", result={"status": "blocked"})
        world.notifier.calls.clear()

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        status_message = world.messages.find_by_id(job.status_message_id)
        assert status_message.payload["state"] == "blocked"
        assert len(world.notifier.calls) == 1


class TestOnResultNotFound:
    def test_offers_closest_purchases(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 80e")
        # A real invoice already recorded for the same merchant, close in amount.
        from app.application.invoice.create_invoice import CreateInvoiceRequest
        from app.domain.entities.invoice import InvoiceType

        world.create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=world.project_a.id,
                created_by=world.user_id,
                type=InvoiceType.MATERIALS_SERVICES,
                issue_date=date(2026, 9, 9),
                recipient_name="Leroy Merlin",
                recipient_address="Arcueil",
                items=[{"description": "x", "quantity": 1, "unit_price": 66.67, "vat_rate": 20.0}],
            )
        )
        job = world.create_job(amount=Decimal("80.00"), job_date=date(2026, 9, 10), reply_to=original)
        world.job_repo.update_result(job.id, status="not_found", result={"status": "not_found"})

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        choices = [m for m in world.messages.messages.values() if m.content_type == "choice"]
        assert len(choices) == 1
        options = choices[0].payload["options"]
        assert any(opt["action"] == "fetch_pick_existing" for opt in options)
        assert any(opt["action"] == "fetch_none" for opt in options)

    def test_never_offers_a_purchase_on_a_foreign_companys_project(self, session) -> None:
        """NEW-H1: `_closest_purchases` must never search a project of a company OTHER
        than the job's own channel — even when the asker also belongs to that company
        and has a matching purchase already recorded there. `on_result` rebuilds its
        scope from the job's stored `channel_key` (M1), so the company bound must
        survive that round trip too."""
        world = World(session)
        foreign_company_id = uuid4()
        foreign_project = _project("Chantier Confidentiel")
        world.project_repo = FakeProjectRepo([world.project_a, foreign_project])
        world.authz_reader = FakeAuthzReader(
            world.company_id, project_companies={foreign_project.id: foreign_company_id}
        )
        world.company_access = FakeCompanyAccessRepo(world.company_id, foreign_company_id)
        world.feature._project_repo = world.project_repo
        world.feature._authz_reader = world.authz_reader
        world.feature._company_access = world.company_access

        from app.application.invoice.create_invoice import CreateInvoiceRequest
        from app.domain.entities.invoice import InvoiceType

        world.create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=foreign_project.id,
                created_by=world.user_id,
                type=InvoiceType.MATERIALS_SERVICES,
                issue_date=date(2026, 9, 9),
                recipient_name="Leroy Merlin",
                recipient_address="Elsewhere",
                items=[{"description": "x", "quantity": 1, "unit_price": 66.67, "vat_rate": 20.0}],
            )
        )
        original = world.post_user_message("va chercher la facture Leroy Merlin 80e")
        job = world.create_job(amount=Decimal("80.00"), job_date=date(2026, 9, 10), reply_to=original)
        world.job_repo.update_result(job.id, status="not_found", result={"status": "not_found"})

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        choices = [m for m in world.messages.messages.values() if m.content_type == "choice"]
        assert choices == []
        for message in world.messages.messages.values():
            assert "Confidentiel" not in (message.body or "")
            assert message.payload is None or "Confidentiel" not in str(message.payload)

    def test_no_candidates_falls_back_to_text(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 999e")
        job = world.create_job(amount=Decimal("999.00"), reply_to=original)
        world.job_repo.update_result(job.id, status="not_found", result={"status": "not_found"})

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        choices = [m for m in world.messages.messages.values() if m.content_type == "choice"]
        assert choices == []
        texts = [m for m in world.messages.messages.values() if m.content_type == "text"]
        assert len(texts) >= 1


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler-utils (pdfinfo) not installed")
class TestOnResultDone:
    def test_runs_ticket_pipeline_and_creates_invoice(self, session) -> None:
        world = World(session)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(amount=Decimal("79.54"), job_date=date(2026, 9, 10), reply_to=original)

        pdf_key = f"assistant/jobs/{job.id}.pdf"
        world.storage.put(pdf_key, io.BytesIO(_pdf_bytes()), content_type="application/pdf")
        world.job_repo.update_result(job.id, status="done", pdf_storage_key=pdf_key, result={"status": "done"})

        from app.application.assistant.models import Invoice as AssistantInvoice

        world.vision._json_answers = [
            AssistantInvoice(merchant="Leroy Merlin", total_ttc=79.54, readability=0.9, date="2026-09-10")
        ]
        world.decisions._fixed = None  # use the keyword-oracle default (single-project auto-pick isn't gated here)

        from app.application.assistant.ports import Decision

        world.decisions._fixed = Decision(
            choices={
                "project": (str(world.project_a.id), 0.95, {}),
                "category": ("materiaux", 0.9, {}),
                "duplicate_of": ("none", 0.95, {}),
            },
            nouls={"amounts_consistent": 0.95},
        )

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        status_message = world.messages.find_by_id(job.status_message_id)
        assert status_message.payload["state"] == "done"
        cards = [m for m in world.messages.messages.values() if m.content_type == "card"]
        assert len(cards) == 1
        assert cards[0].payload["card"]["badge"] == "confirmed"
        invoices = world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2027, 1, 1))
        assert len(invoices) == 1


class TestChannelRoundTrip:
    """Phase 03's answer to phase 01/02's open question 2: `fetch_invoice`'s job carries
    the originating channel, and `on_result` posts the final reply back into it."""

    def test_fetch_invoice_stamps_channel_key_on_job_creation(self, session) -> None:
        world = World(session)
        channel = ChannelRef(kind="project", id=world.project_a.id)
        message = world.post_user_message("va chercher la facture Leroy Merlin 79,54 e d'hier")
        world.vision._json_answers = [AmountDate(amount_ttc=79.54, date="2026-09-20")]
        decision = RouterDecision(intent="fetch_invoice", intent_confidence=0.95, merchant="leroymerlin")

        world.feature.fetch_invoice(
            user_id=world.user_id,
            message_id=message.id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            decision=decision,
            scope=ChannelScope(
                kind="project",
                company_id=world.company_id,
                project_id=world.project_a.id,
                is_admin_channel=False,
                asker_id=world.user_id,
            ),
        )

        job = world.job_repo.list_recent_for_user(world.user_id, limit=1)[0]
        assert job.channel_key == channel.key

    def test_on_result_posts_the_invoice_card_into_the_jobs_channel(self, session) -> None:
        world = World(session)
        channel = ChannelRef(kind="project", id=world.project_a.id)
        original = world.post_user_message("va chercher la facture Leroy Merlin 79,54e")
        job = world.create_job(
            amount=Decimal("79.54"), job_date=date(2026, 9, 10), reply_to=original, channel_key=channel.key
        )

        pdf_key = f"assistant/jobs/{job.id}.pdf"
        world.storage.put(pdf_key, io.BytesIO(_pdf_bytes()), content_type="application/pdf")
        world.job_repo.update_result(job.id, status="done", pdf_storage_key=pdf_key, result={"status": "done"})

        from app.application.assistant.models import Invoice as AssistantInvoice
        from app.application.assistant.ports import Decision

        world.vision._json_answers = [
            AssistantInvoice(merchant="Leroy Merlin", total_ttc=79.54, readability=0.9, date="2026-09-10")
        ]
        world.decisions._fixed = Decision(
            choices={
                "project": (str(world.project_a.id), 0.95, {}),
                "category": ("materiaux", 0.9, {}),
                "duplicate_of": ("none", 0.95, {}),
            },
            nouls={"amounts_consistent": 0.95},
        )

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        cards = [m for m in world.messages.messages.values() if m.content_type == "card"]
        assert len(cards) == 1
        assert cards[0].channel == channel


class TestHandleAction:
    def test_fetch_pick_existing_posts_the_invoice_card(self, session) -> None:
        world = World(session)
        from app.application.invoice.create_invoice import CreateInvoiceRequest
        from app.domain.entities.invoice import InvoiceType

        response = world.create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=world.project_a.id,
                created_by=world.user_id,
                type=InvoiceType.MATERIALS_SERVICES,
                issue_date=date(2026, 9, 9),
                recipient_name="Leroy Merlin",
                recipient_address="Arcueil",
                items=[{"description": "x", "quantity": 1, "unit_price": 66.67, "vat_rate": 20.0}],
            )
        )
        choice_message_id = uuid4()

        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=choice_message_id,
            action="fetch_pick_existing",
            payload={"invoice_id": response.id},
            lang="fr",
            messenger=world.messenger,
            trace_id="t",
            scope=world.default_scope(),
        )

        assert handled is True
        cards = [m for m in world.messages.messages.values() if m.content_type == "card"]
        assert len(cards) == 1
        assert cards[0].payload["card"]["id"] == response.id
        assert cards[0].payload["card"]["project_id"] == str(world.project_a.id)

    def test_fetch_pick_existing_refuses_to_disclose_an_invoice_on_a_foreign_project(self, session) -> None:
        """C1's defense-in-depth layer (pass-2 review, previously untested): the primary
        defense is `SubmitAssistantActionUseCase`'s stored-option equality check, but
        this handler independently re-checks that the invoice is on a project the caller
        can actually see before ever posting its card — closing the disclosure even if a
        forged `invoice_id` ever reached this far."""
        world = World(session)
        from app.application.invoice.create_invoice import CreateInvoiceRequest
        from app.domain.entities.invoice import InvoiceType

        foreign_project_id = uuid4()  # never registered in world.project_repo
        foreign = world.create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=foreign_project_id,
                created_by=uuid4(),
                type=InvoiceType.MATERIALS_SERVICES,
                issue_date=date(2026, 9, 9),
                recipient_name="Foreign Corp",
                recipient_address="Elsewhere",
                items=[{"description": "x", "quantity": 1, "unit_price": 10.0, "vat_rate": 20.0}],
            )
        )

        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=uuid4(),
            action="fetch_pick_existing",
            payload={"invoice_id": foreign.id},
            lang="fr",
            messenger=world.messenger,
            trace_id="t",
            scope=world.default_scope(),
        )

        assert handled is True
        assert [m for m in world.messages.messages.values() if m.content_type == "card"] == []

    def test_fetch_pick_existing_refuses_an_invoice_on_a_writable_project_of_a_different_company(self, session) -> None:
        """NEW-H1: unlike the forged/never-registered case above, this project genuinely
        exists and the asker genuinely belongs to its company — but NOT the channel this
        tap came from. `writable_projects` must bound to the channel's own company, not
        every company the asker belongs to, or this card would disclose another
        tenant's invoice."""
        world = World(session)
        foreign_company_id = uuid4()
        foreign_project = _project("Chantier Confidentiel")
        world.project_repo = FakeProjectRepo([world.project_a, foreign_project])
        world.authz_reader = FakeAuthzReader(
            world.company_id, project_companies={foreign_project.id: foreign_company_id}
        )
        world.company_access = FakeCompanyAccessRepo(world.company_id, foreign_company_id)
        world.feature._project_repo = world.project_repo
        world.feature._authz_reader = world.authz_reader
        world.feature._company_access = world.company_access

        from app.application.invoice.create_invoice import CreateInvoiceRequest
        from app.domain.entities.invoice import InvoiceType

        foreign = world.create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=foreign_project.id,
                created_by=world.user_id,
                type=InvoiceType.MATERIALS_SERVICES,
                issue_date=date(2026, 9, 9),
                recipient_name="Leroy Merlin",
                recipient_address="Elsewhere",
                items=[{"description": "x", "quantity": 1, "unit_price": 66.67, "vat_rate": 20.0}],
            )
        )

        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=uuid4(),
            action="fetch_pick_existing",
            payload={"invoice_id": foreign.id},
            lang="fr",
            messenger=world.messenger,
            trace_id="t",
            scope=world.default_scope(),
        )

        assert handled is True
        assert [m for m in world.messages.messages.values() if m.content_type == "card"] == []

    def test_fetch_none_asks_for_the_ticket_photo(self, session) -> None:
        world = World(session)
        choice_message_id = uuid4()

        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=choice_message_id,
            action="fetch_none",
            payload={},
            lang="fr",
            messenger=world.messenger,
            trace_id="t",
            scope=world.default_scope(),
        )

        assert handled is True
        texts = [m for m in world.messages.messages.values() if m.content_type == "text"]
        assert len(texts) == 1
        assert "ticket" in texts[0].body

    def test_unknown_action_returns_false(self, session) -> None:
        world = World(session)
        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=uuid4(),
            action="something_else",
            payload={},
            lang="fr",
            messenger=world.messenger,
            trace_id="t",
            scope=world.default_scope(),
        )
        assert handled is False
