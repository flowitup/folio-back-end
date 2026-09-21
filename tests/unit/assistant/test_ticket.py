"""Unit tests for `app.application.assistant.features.ticket.TicketFeature` (feature C).

Uses REAL `SQLAlchemyInvoiceRepository`/`SQLAlchemyInvoiceAttachmentRepository` and REAL
`CreateInvoiceUseCase`/`DeleteInvoiceUseCase`/`UploadAttachmentUseCase` against an
in-memory SQLite DB (the `session` fixture) so "the created invoice's total equals the
extracted TTC" and "two attachments were recorded" are proven against real persistence,
not a canned fake. Projects/labor/authz are simple in-memory fakes — the permission
matrix itself is covered by `test_state.py`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import cv2
import numpy as np
import pytest

from app.application.assistant.features.ticket import TicketFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import Invoice
from app.application.assistant.ports import Decision
from app.application.invoice.create_invoice import CreateInvoiceUseCase
from app.application.invoice.delete_invoice import DeleteInvoiceUseCase
from app.application.invoice.upload_attachment import UploadAttachmentUseCase
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.domain.entities.project import Project
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.adapters.sqlalchemy_invoice_attachment import SQLAlchemyInvoiceAttachmentRepository
from app.infrastructure.database.repositories.sqlalchemy_assistant_import_repository import (
    SqlAlchemyAssistantImportRepository,
)
from tests.fakes.ai import RecordingImageGen, ScriptedDecision, ScriptedVision


# ---------------------------------------------------------------------------
# Fakes: projects, labor, authz, chat message repo
# ---------------------------------------------------------------------------


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._by_id = {p.id: p for p in projects}

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return list(self._by_id.values())

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return self._by_id.get(project_id)


class FakeAuthzReader:
    """Grants `project:manage_invoices` on every project (the permission matrix itself
    is exercised for real in `test_state.py`)."""

    def company_role_for(self, user_id: UUID, company_id: UUID) -> Optional[str]:
        return "manager"

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return uuid4()

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
    def __init__(self, company_id: UUID) -> None:
        self._company_id = company_id

    def list_for_user(self, user_id: UUID) -> list[_Access]:
        return [_Access(company_id=self._company_id)]


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


def _photo_bytes() -> bytes:
    canvas = np.zeros((400, 300, 3), dtype=np.uint8)
    cv2.rectangle(canvas, (40, 40), (260, 360), (255, 255, 255), -1)
    ok, buffer = cv2.imencode(".jpg", canvas)
    assert ok
    return buffer.tobytes()


def _project(name: str = "Villa Arcueil") -> Project:
    return Project(id=uuid4(), name=name, owner_id=uuid4(), created_at=datetime.now(timezone.utc), address="12 rue X")


# ---------------------------------------------------------------------------
# World fixture
# ---------------------------------------------------------------------------


class World:
    def __init__(self, session, scan_mode: str = "opencv") -> None:
        self.session = session
        self.user_id = uuid4()
        self.project_a = _project("Villa Arcueil")
        self.project_b = _project("Extension Meaux")
        self.project_repo = FakeProjectRepo([self.project_a, self.project_b])
        self.authz_reader = FakeAuthzReader()
        self.company_access = FakeCompanyAccessRepo(uuid4())
        self.labor_entry_repo = FakeLaborEntryRepo()
        self.worker_repo = FakeWorkerRepo()
        self.invoice_repo = SQLAlchemyInvoiceRepository(session)
        self.attachment_repo = SQLAlchemyInvoiceAttachmentRepository(session)
        self.import_repo = SqlAlchemyAssistantImportRepository(session)
        self.storage = InMemoryDocumentStorage()
        self.messages = FakeMessageRepo()
        self.messenger = AssistantMessenger(self.messages, FakeSession())
        self.create_invoice_usecase = CreateInvoiceUseCase(self.invoice_repo)
        self.delete_invoice_usecase = DeleteInvoiceUseCase(self.invoice_repo, self.attachment_repo, self.storage)
        self.upload_attachment_usecase = UploadAttachmentUseCase(self.invoice_repo, self.attachment_repo, self.storage)
        self.vision = ScriptedVision(json_answers=[])
        self.decisions = ScriptedDecision()
        self.image_gen = RecordingImageGen()
        self.feature = TicketFeature(
            vision=self.vision,
            decisions=self.decisions,
            image_gen=self.image_gen,
            scan_mode=scan_mode,
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

    def post_photo(self) -> UUID:
        photo_bytes = _photo_bytes()
        key = f"chat/{uuid4()}"
        self.storage.put(key, io.BytesIO(photo_bytes), content_type="image/jpeg")
        message = ChatMessage.create(
            channel=ChannelRef(kind="assistant", id=self.user_id),
            sender_id=self.user_id,
            body=None,
            attachment=ChatAttachment(
                storage_key=key, filename="ticket.jpg", content_type="image/jpeg", size_bytes=len(photo_bytes)
            ),
        )
        self.messages.add(message)
        return message.id

    def last_replies(self) -> list[ChatMessage]:
        return sorted(self.messages.messages.values(), key=lambda m: m.created_at)


@pytest.fixture
def world(session) -> World:
    return World(session)


def _project_decision(
    project_id: UUID,
    project_confidence: float,
    category: str = "materiaux",
    duplicate: str = "none",
    duplicate_confidence: float = 0.0,
    amounts_consistent: float = 0.9,
) -> Decision:
    return Decision(
        choices={
            "project": (str(project_id), project_confidence, {}),
            "category": (category, 0.9, {}),
            "duplicate_of": (duplicate, duplicate_confidence, {}),
        },
        nouls={"amounts_consistent": amounts_consistent},
    )


_S3_KEYS = frozenset({"project", "category", "duplicate_of", "amounts_consistent"})
_ATTACH_KEYS = frozenset({"attach_to"})
_VERIFY_KEYS = frozenset({"faithful", "worst_diff"})


class TestCreateConfirmed:
    def test_creates_invoice_with_matching_total_and_two_attachments(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", date="2026-09-10", total_ttc=50.0, readability=0.9)]
        world.decisions._by_question_keys = {_S3_KEYS: _project_decision(world.project_a.id, 0.95)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        invoices = world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31))
        assert len(invoices) == 1
        invoice = invoices[0]
        assert abs(float(invoice.total_amount) - 50.0) <= 0.02
        attachments = world.attachment_repo.list_by_invoice(invoice.id)
        assert len(attachments) == 2
        assert {a.mime_type for a in attachments} == {"image/jpeg", "application/pdf"}
        import_row = world.import_repo.find_by_invoice(invoice.id)
        assert import_row is not None
        assert import_row.status == "confirmed"
        assert import_row.source == "ticket"

    def test_unreadable_photo_asks_to_retake(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", total_ttc=50.0, readability=0.1)]

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        replies = world.last_replies()
        assert replies[-1].content_type == "text"
        assert "reprendre" in (replies[-1].body or "")


class TestDuplicateRefused:
    def test_refuses_and_posts_the_existing_invoice_card(self, world: World) -> None:
        existing = world.create_invoice_usecase.execute(
            _create_request(world.project_a.id, world.user_id, "Point P", date(2026, 9, 8), 79.50)
        )
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", date="2026-09-10", total_ttc=79.54, readability=0.9)]
        world.decisions._by_question_keys = {
            _ATTACH_KEYS: Decision(choices={"attach_to": ("new", 0.99, {})}, nouls={}),
            _S3_KEYS: _project_decision(world.project_a.id, 0.95, duplicate=existing.id, duplicate_confidence=0.9),
        }

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        invoices = world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31))
        assert len(invoices) == 1  # nothing new was created
        replies = world.last_replies()
        assert any(r.content_type == "card" for r in replies)
        assert any("doublon" in (r.body or "") or "déjà" in (r.body or "") for r in replies) or any(
            r.content_type == "text" for r in replies
        )


class TestToConfirmWithMultipleProjects:
    """S3's project confidence lands in the `to_confirm` band (>= PROJECT_ASK_LOW,
    < PROJECT_CONFIRMED) with more than one writable project — decision D13 (review
    finding C2 removed the old behaviour: create immediately, then offer a "wrong
    chantier?" button that deleted and recreated the invoice on tap, non-atomically
    dropping payment/refund/highlight/worker links). The new behaviour never creates
    until an explicit tap, exactly like the below-threshold multi-project case."""

    def test_does_not_create_until_the_tap(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", date="2026-09-10", total_ttc=50.0, readability=0.9)]
        world.decisions._by_question_keys = {_S3_KEYS: _project_decision(world.project_a.id, 0.75)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        assert (
            world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31)) == []
        )
        assert (
            world.invoice_repo.find_by_project_in_range(world.project_b.id, date(2026, 1, 1), date(2026, 12, 31)) == []
        )
        choice = next(m for m in world.last_replies() if m.content_type == "choice")
        options = choice.payload["options"]
        # The fake Jev decision carries no per-candidate `project` probabilities, so only
        # S3's own pick (project_a) is offered — a real Jev call would rank a second
        # candidate too (see `_top_candidate_projects`), but even a single option still
        # requires an explicit tap before anything is written (hard rule 2).
        assert len(options) == 1
        assert options[0]["action"] == "set_project"
        assert options[0]["payload"]["project_id"] == str(world.project_a.id)
        assert "invoice" in options[0]["payload"]

        world.feature.handle_action(
            user_id=world.user_id,
            message_id=choice.id,
            action="set_project",
            payload=options[0]["payload"],
            lang="fr",
            messenger=world.messenger,
            trace_id="t2",
        )

        created = world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31))
        assert len(created) == 1
        import_row = world.import_repo.find_by_invoice(created[0].id)
        assert import_row is not None
        assert import_row.status == "confirmed"


class TestPickProjectAllBelowThreshold:
    def test_does_not_create_until_the_tap(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", date="2026-09-10", total_ttc=50.0, readability=0.9)]
        world.decisions._by_question_keys = {_S3_KEYS: _project_decision(world.project_a.id, 0.2)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        assert (
            world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31)) == []
        )
        assert (
            world.invoice_repo.find_by_project_in_range(world.project_b.id, date(2026, 1, 1), date(2026, 12, 31)) == []
        )
        choice = next(m for m in world.last_replies() if m.content_type == "choice")
        options = choice.payload["options"]
        assert len(options) == 2
        assert all(option["action"] == "set_project" and "invoice" in option["payload"] for option in options)

        picked = next(option for option in options if option["payload"]["project_id"] == str(world.project_b.id))
        world.feature.handle_action(
            user_id=world.user_id,
            message_id=choice.id,
            action="set_project",
            payload=picked["payload"],
            lang="fr",
            messenger=world.messenger,
            trace_id="t2",
        )

        created = world.invoice_repo.find_by_project_in_range(world.project_b.id, date(2026, 1, 1), date(2026, 12, 31))
        assert len(created) == 1
        assert len(world.attachment_repo.list_by_invoice(created[0].id)) == 2
        import_row = world.import_repo.find_by_invoice(created[0].id)
        assert import_row is not None
        assert import_row.status == "confirmed"


class TestAttachToExisting:
    def test_attaches_instead_of_creating_a_new_invoice(self, world: World) -> None:
        existing = world.create_invoice_usecase.execute(
            _create_request(world.project_a.id, world.user_id, "Leroy Merlin", date(2026, 9, 10), 66.28)
        )
        message_id = world.post_photo()
        world.vision._json_answers = [
            Invoice(merchant="Leroy Merlin", date="2026-09-10", total_ttc=float(existing.total_amount), readability=0.9)
        ]
        world.decisions._by_question_keys = {
            _ATTACH_KEYS: Decision(choices={"attach_to": (str(existing.id), 0.95, {})}, nouls={})
        }

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        attachments = world.attachment_repo.list_by_invoice(UUID(existing.id))
        assert len(attachments) == 2
        import_row = world.import_repo.find_by_invoice(UUID(existing.id))
        assert import_row is not None
        assert import_row.status == "confirmed"
        assert import_row.source == "ticket"
        # No second invoice was created for the same ticket.
        all_invoices = world.invoice_repo.find_by_project_in_range(
            world.project_a.id, date(2026, 1, 1), date(2026, 12, 31)
        )
        assert len(all_invoices) == 1


class TestGenaiFallsBackToOpenCv:
    def test_falls_back_when_jev_verify_never_passes(self, world: World) -> None:
        world.feature._scan_mode = "genai"
        message_id = world.post_photo()
        invoice_a = Invoice(merchant="Point P", date="2026-09-10", total_ttc=50.0, readability=0.9)
        world.vision._json_answers = [invoice_a]
        world.decisions._by_question_keys = {
            _VERIFY_KEYS: Decision(choices={"worst_diff": ("amount", 0.9, {})}, nouls={"faithful": 0.2}),
            _S3_KEYS: _project_decision(world.project_a.id, 0.95),
        }

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        created = world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31))
        assert len(created) == 1
        attachments = world.attachment_repo.list_by_invoice(created[0].id)
        assert len(attachments) == 2
        pdf_attachment = next(a for a in attachments if a.mime_type == "application/pdf")
        stream, _length = world.storage.get_stream(pdf_attachment.storage_key)
        assert stream.read().startswith(b"%PDF-")
        # Gemini was called (and re-tried once) before falling back.
        assert len(world.image_gen.calls) == 2


class TestDefenseInDepthPendingKeyPrefix:
    """C1's defense-in-depth layer (pass-2 review, previously untested): the primary
    defense is `SubmitAssistantActionUseCase`'s stored-option equality check, but
    `_fetch_pending`/`_cleanup_pending` independently refuse any storage key outside
    `assistant/pending/` — a forged `original_key` pointing at another channel's photo
    (or any other S3 key) must never be read or deleted through this path, even if a
    future regression let a forged payload reach this far."""

    def test_original_key_outside_the_pending_prefix_is_refused(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [Invoice(merchant="Point P", date="2026-09-10", total_ttc=50.0, readability=0.9)]
        world.decisions._by_question_keys = {_S3_KEYS: _project_decision(world.project_a.id, 0.75)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )
        choice = next(m for m in world.last_replies() if m.content_type == "choice")
        forged_payload = dict(choice.payload["options"][0]["payload"])
        forged_payload["original_key"] = "chat/some-other-channels-photo"

        world.feature.handle_action(
            user_id=world.user_id,
            message_id=choice.id,
            action="set_project",
            payload=forged_payload,
            lang="fr",
            messenger=world.messenger,
            trace_id="t2",
        )

        assert (
            world.invoice_repo.find_by_project_in_range(world.project_a.id, date(2026, 1, 1), date(2026, 12, 31)) == []
        )
        # _fetch_pending refused the key -> original_bytes is None -> the generic error
        # template, never a created/confirmed reply.
        assert world.last_replies()[-1].content_type == "text"


class TestDefenseInDepthConfirmDuplicateCrossProject:
    """`confirm_duplicate` must never disclose (via its card) an invoice on a project the
    caller cannot see, even if a forged `candidate_invoice_id` ever reached this handler."""

    def test_refuses_to_disclose_an_invoice_on_a_foreign_project(self, world: World) -> None:
        foreign_project_id = uuid4()  # never registered in world.project_repo
        foreign = world.create_invoice_usecase.execute(
            _create_request(foreign_project_id, world.user_id, "Foreign Corp", date(2026, 9, 1), 42.0)
        )

        world.feature.handle_action(
            user_id=world.user_id,
            message_id=uuid4(),
            action="confirm_duplicate",
            payload={"candidate_invoice_id": str(foreign.id), "original_key": "", "scan_key": None},
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
        )

        replies = world.last_replies()
        assert not any(r.content_type == "card" for r in replies)
        assert any(r.content_type == "text" for r in replies)


def _create_request(project_id: UUID, user_id: UUID, merchant: str, issue_date: date, total_ttc: float):
    from app.application.invoice.create_invoice import CreateInvoiceRequest
    from app.domain.entities.invoice import InvoiceType

    return CreateInvoiceRequest(
        project_id=project_id,
        created_by=user_id,
        type=InvoiceType.MATERIALS_SERVICES,
        issue_date=issue_date,
        recipient_name=merchant,
        items=[{"description": "Ciment", "quantity": 1, "unit_price": total_ttc, "vat_rate": 0}],
    )
