"""Feature C — ticket/receipt photo -> clean scan -> matched or new invoice.

Pipeline (plan section 3/4, S1-S5):
  C1 extract the ORIGINAL photo (S1, ``extract.py``) — every number downstream comes
     from this extraction, never from the generated scan (plan hard rule).
  C2 produce a scan PDF: ``SCAN_MODE=genai`` asks Gemini to redraw the ticket, re-extracts
     the generated image and Jev-verifies it is faithful to the original (retry once,
     else fall back to OpenCV); ``SCAN_MODE=opencv`` always uses ``scanify.py``.
  C3 ``match_invoice``: an already-recorded invoice this ticket might just be the scan
     of (same merchant, amount, +/- 5 days, no scan yet) — Jev decides whether to attach
     instead of creating a new invoice.
  C4 attach (conf >= gate.ATTACH_EXISTING) or S2 state -> S3 one Jev call
     (``decide.decide_ticket``) -> S4 gate (this module) -> create/ask/refuse.

Every write (attach, create) is gated on a Jev confidence from ``gate.py`` — no
exceptions. Deferred flows (duplicate check, "pick a project") persist the original
photo + scan PDF under a temporary storage key (``assistant/pending/<trace_id>/...``)
inside the choice payload, since ``invoice_attachments`` rows require an invoice that
does not exist yet; the create-on-tap action re-reads them and re-uploads under the
real invoice id once it exists.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from app.application.assistant import gate, reply
from app.application.assistant.decide import TicketDecision, decide_ticket
from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.extract import extract_invoice, is_readable
from app.application.assistant.features._photos import read_photo_bytes
from app.application.assistant.import_ports import InvoiceImportRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import Invoice
from app.application.assistant.ports import (
    ChoiceQuestion,
    DecisionPort,
    ImageGenPort,
    MessagePosterPort,
    NoulQuestion,
    VisionLlmPort,
)
from app.application.assistant.scanify import scanify, to_pdf
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
from app.application.authz.ports import AuthzReaderPort
from app.application.chat.ports import ChatAttachmentStoragePort
from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.invoice.create_invoice import CreateInvoiceRequest, CreateInvoiceUseCase
from app.application.invoice.delete_invoice import DeleteInvoiceUseCase
from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceAttachmentRepository, IInvoiceRepository
from app.application.invoice.upload_attachment import UploadAttachmentUseCase
from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.invoice import Invoice as InvoiceEntity, InvoiceType

logger = logging.getLogger(__name__)

# Keep byte-identical across every call — same rule as extract.py's S1 prompt.
SCAN_PROMPT_FR = (
    "Redessine ce ticket de caisse ou cette facture comme un scan de document propre et bien éclairé, à plat, "
    "sans ombre ni reflet. NE MODIFIE AUCUN TEXTE, AUCUN MONTANT, AUCUNE DATE ET AUCUNE LIGNE : conserve "
    "exactement les mêmes informations que l'original, uniquement l'aspect visuel change."
)

_VERIFY_WORST_DIFF_CRITERIA: dict[str, Optional[str]] = {
    "none": "aucune divergence",
    "amount": "un montant diffère",
    "date": "une date diffère",
    "number": "un numéro diffère",
    "lines": "des lignes diffèrent",
    "merchant": "le nom du commerçant diffère",
}

#: How many genai scan attempts before falling back to OpenCV (initial + one retry).
_GENAI_ATTEMPTS = 2
#: How many candidate projects the "pick a project" correction choice offers.
_TOP_PROJECTS_FOR_CORRECTION = 2


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _line_vat_rate(invoice: Invoice) -> float:
    return invoice.tva_rates[0] if len(invoice.tva_rates) == 1 else 20.0


def _build_line_items(invoice: Invoice) -> list[dict[str, Any]]:
    """Per-line items when every line has a total, else one summary line (plan section 4)."""
    if invoice.lines and all(line.total_ttc is not None for line in invoice.lines):
        vat_rate = _line_vat_rate(invoice)
        items = []
        for line in invoice.lines:
            if line.total_ttc is None:  # pragma: no cover - excluded by the `all(...)` check above
                continue
            qty = float(line.qty) if line.qty else 1.0
            total_ttc = float(line.total_ttc)
            unit_price_ht = (total_ttc / qty) / (1 + vat_rate / 100.0) if qty else total_ttc
            items.append(
                {"description": line.label, "quantity": qty, "unit_price": unit_price_ht, "vat_rate": vat_rate}
            )
        return items
    return _build_single_line_item(invoice)


def _build_single_line_item(invoice: Invoice) -> list[dict[str, Any]]:
    vat_rate = _line_vat_rate(invoice)
    if invoice.total_ht is not None:
        unit_price_ht = invoice.total_ht
    else:
        unit_price_ht = invoice.total_ttc / (1 + vat_rate / 100.0)
    label = f"Ticket {invoice.merchant} {invoice.date}".strip() if invoice.date else f"Ticket {invoice.merchant}"
    return [{"description": label, "quantity": 1, "unit_price": unit_price_ht, "vat_rate": vat_rate}]


class TicketFeature:
    """Implements feature C end to end: run() for a fresh photo, handle_action() for taps."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        decisions: DecisionPort,
        image_gen: ImageGenPort,
        scan_mode: str,
        messages: MessagePosterPort,
        storage: ChatAttachmentStoragePort,
        company_access: UserCompanyAccessRepositoryPort,
        project_repo: IProjectRepository,
        authz_reader: AuthzReaderPort,
        invoice_repo: IInvoiceRepository,
        attachment_repo: IInvoiceAttachmentRepository,
        worker_repo: IWorkerRepository,
        labor_entry_repo: ILaborEntryRepository,
        import_repo: InvoiceImportRepositoryPort,
        create_invoice_usecase: CreateInvoiceUseCase,
        delete_invoice_usecase: DeleteInvoiceUseCase,
        upload_attachment_usecase: UploadAttachmentUseCase,
    ) -> None:
        self._vision = vision
        self._decisions = decisions
        self._image_gen = image_gen
        self._scan_mode = scan_mode
        self._messages = messages
        self._storage = storage
        self._company_access = company_access
        self._project_repo = project_repo
        self._authz_reader = authz_reader
        self._invoice_repo = invoice_repo
        self._attachment_repo = attachment_repo
        self._worker_repo = worker_repo
        self._labor_entry_repo = labor_entry_repo
        self._import_repo = import_repo
        self._create_invoice_usecase = create_invoice_usecase
        self._delete_invoice_usecase = delete_invoice_usecase
        self._upload_attachment_usecase = upload_attachment_usecase

    # ------------------------------------------------------------------
    # Entry point — a fresh photo (C1-C4, S2-S4)
    # ------------------------------------------------------------------

    def run(self, *, user_id: UUID, message_id: UUID, lang: str, messenger: AssistantMessenger, trace_id: str) -> None:
        photo = read_photo_bytes(self._messages, self._storage, message_id)
        if photo is None:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return
        photo_bytes, photo_filename, photo_mime = photo

        try:
            invoice_a = extract_invoice(self._vision, [photo_bytes])
        except LlmOutputError:
            messenger.post_text(user_id, reply.render("retake_photo", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        if not is_readable(invoice_a):
            messenger.post_text(user_id, reply.render("retake_photo", lang), reply_to_id=message_id, trace_id=trace_id)
            return

        company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
        projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
        today = date.today()
        ticket_day = _parse_date(invoice_a.date) or today

        candidates = match_candidates(self._invoice_repo, self._import_repo, projects, invoice_a, ticket_day)
        if candidates:
            picked, attach_confidence = self._decide_attach(candidates, invoice_a)
            if picked is not None and gate.attach_existing_allowed(attach_confidence):
                project, existing = picked
                scan_pdf, _mode = self._make_scan(invoice_a, photo_bytes)
                self._attach_to_existing(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    project=project,
                    existing=existing,
                    confidence=attach_confidence,
                    photo_bytes=photo_bytes,
                    photo_mime=photo_mime,
                    photo_filename=photo_filename,
                    scan_pdf=scan_pdf,
                )
                return

        scan_pdf, _mode = self._make_scan(invoice_a, photo_bytes)

        workers_by_project = {
            str(p.id): workers_on_site(self._labor_entry_repo, self._worker_repo, p.id, ticket_day) for p in projects
        }
        recent_by_project = {
            str(p.id): recent_purchases(self._invoice_repo, p.id, invoice_a.merchant, today) for p in projects
        }
        dup_candidates = duplicate_candidates(
            self._invoice_repo, projects, invoice_a.merchant, invoice_a.total_ttc, ticket_day
        )
        sane = amounts_sane(invoice_a)
        ticket_state = build_ticket_state(
            invoice_a, projects, workers_by_project, recent_by_project, dup_candidates, sane
        )
        decision = decide_ticket(self._decisions, ticket_state, projects, dup_candidates)

        self._apply_gate(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=photo_bytes,
            original_mime=photo_mime,
            original_filename=photo_filename,
            scan_bytes=scan_pdf,
        )

    # ------------------------------------------------------------------
    # C2 — scan generation
    # ------------------------------------------------------------------

    def _make_scan(self, invoice_a: Invoice, photo_bytes: bytes) -> tuple[bytes, str]:
        if self._scan_mode == "genai":
            generated = self._try_genai_scan(invoice_a, photo_bytes)
            if generated is not None:
                return to_pdf(generated), "genai"
        processed = scanify(photo_bytes, self._vision)
        return to_pdf(processed), "opencv"

    def _try_genai_scan(self, invoice_a: Invoice, photo_bytes: bytes) -> Optional[bytes]:
        from app.application.assistant.exceptions import ProviderNotConfiguredError

        for _attempt in range(_GENAI_ATTEMPTS):
            try:
                generated = self._image_gen.generate(photo_bytes, SCAN_PROMPT_FR)
            except ProviderNotConfiguredError:
                return None
            except LlmOutputError:
                continue
            try:
                invoice_b = extract_invoice(self._vision, [generated])
            except LlmOutputError:
                continue
            faithful, worst_diff = self._jev_verify(invoice_a, invoice_b)
            if gate.verify_faithful(faithful) and worst_diff == "none":
                return generated
        return None

    def _jev_verify(self, invoice_a: Invoice, invoice_b: Invoice) -> tuple[float, str]:
        state = {"original": invoice_a.model_dump(), "generated_scan": invoice_b.model_dump()}
        questions: dict[str, ChoiceQuestion | NoulQuestion] = {
            "faithful": NoulQuestion(
                instructions=(
                    "Le scan généré conserve exactement les mêmes montants, dates, numéros et lignes que "
                    "l'original, sans invention ni omission."
                )
            ),
            "worst_diff": ChoiceQuestion(
                instructions="Divergence la plus grave entre l'original et le scan généré, si présente.",
                criteria=dict(_VERIFY_WORST_DIFF_CRITERIA),
            ),
        }
        result = self._decisions.decide(state, questions)
        faithful = result.noul("faithful")
        worst_diff, _confidence, _probabilities = result.choice("worst_diff")
        return faithful, worst_diff

    # ------------------------------------------------------------------
    # C3/C4 — attach to an already-recorded invoice
    # ------------------------------------------------------------------

    def _decide_attach(
        self, candidates: list[tuple[WritableProject, InvoiceEntity]], invoice_a: Invoice
    ) -> tuple[Optional[tuple[WritableProject, InvoiceEntity]], float]:
        criteria: dict[str, Optional[str]] = {"new": "aucune, créer une nouvelle facture"}
        by_key: dict[str, tuple[WritableProject, InvoiceEntity]] = {}
        for project, existing in candidates:
            key = str(existing.id)
            criteria[key] = f"{project.name} – {existing.issue_date.isoformat()} – {float(existing.total_amount)}€"
            by_key[key] = (project, existing)
        state = {
            "ticket": invoice_a.model_dump(),
            "candidates": [{"id": key, "label": label} for key, label in criteria.items() if key != "new"],
        }
        questions: dict[str, ChoiceQuestion | NoulQuestion] = {
            "attach_to": ChoiceQuestion(
                instructions=(
                    "La facture déjà enregistrée dont ce ticket est probablement le scan (même commerçant, "
                    "montant proche, date proche), sinon 'new'."
                ),
                criteria=criteria,
            )
        }
        result = self._decisions.decide(state, questions)
        label, confidence, _probabilities = result.choice("attach_to")
        if label == "new" or label not in by_key:
            return None, confidence
        return by_key[label], confidence

    def _attach_to_existing(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        project: WritableProject,
        existing: InvoiceEntity,
        confidence: float,
        photo_bytes: bytes,
        photo_mime: str,
        photo_filename: str,
        scan_pdf: bytes,
    ) -> None:
        original_attachment = self._upload_attachment_usecase.execute(
            invoice_id=existing.id,
            filename=photo_filename,
            mime_type=photo_mime,
            size_bytes=len(photo_bytes),
            fileobj=io.BytesIO(photo_bytes),
            uploaded_by=user_id,
        )
        scan_attachment = self._upload_attachment_usecase.execute(
            invoice_id=existing.id,
            filename=f"scan-{existing.invoice_number}.pdf",
            mime_type="application/pdf",
            size_bytes=len(scan_pdf),
            fileobj=io.BytesIO(scan_pdf),
            uploaded_by=user_id,
        )
        existing_import = self._import_repo.find_by_invoice(existing.id)
        status = existing_import.status if existing_import is not None else "confirmed"
        self._import_repo.add_invoice_import(
            invoice_id=existing.id,
            status=status,
            source="ticket",
            ai_confidence=confidence,
            original_attachment_id=original_attachment.id,
            scan_attachment_id=scan_attachment.id,
            trace_id=trace_id,
        )
        messenger.post_text(
            user_id,
            reply.render("invoice_attached", lang, number=existing.invoice_number),
            reply_to_id=message_id,
            trace_id=trace_id,
        )
        card = self._invoice_card(
            invoice_id=existing.id,
            invoice_number=existing.invoice_number,
            project_name=project.name,
            merchant=existing.recipient_name,
            total_ttc=float(existing.total_amount),
            issue_date=existing.issue_date.isoformat(),
            status=status,
        )
        messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)

    # ------------------------------------------------------------------
    # S4 — gate: duplicate check, then project assignment
    # ------------------------------------------------------------------

    def _apply_gate(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        invoice_a: Invoice,
        projects: list[WritableProject],
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
    ) -> None:
        if decision.duplicate_of is not None:
            dup_status = gate.duplicate_status(decision.duplicate_confidence)
            if dup_status == "reject":
                self._post_duplicate_refused(
                    user_id, message_id, lang, messenger, trace_id, decision.duplicate_of, projects
                )
                return
            if dup_status == "ask":
                self._post_duplicate_check(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    invoice_a=invoice_a,
                    decision=decision,
                    original_bytes=original_bytes,
                    original_mime=original_mime,
                    original_filename=original_filename,
                    scan_bytes=scan_bytes,
                )
                return
        self._resolve_project_and_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=original_bytes,
            original_mime=original_mime,
            original_filename=original_filename,
            scan_bytes=scan_bytes,
        )

    def _post_duplicate_refused(
        self,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        duplicate_of: UUID,
        projects: list[WritableProject],
    ) -> None:
        existing = self._invoice_repo.find_by_id(duplicate_of)
        if existing is not None:
            project_name = self._project_name(existing.project_id, projects)
            card = self._invoice_card(
                invoice_id=existing.id,
                invoice_number=existing.invoice_number,
                project_name=project_name,
                merchant=existing.recipient_name,
                total_ttc=float(existing.total_amount),
                issue_date=existing.issue_date.isoformat(),
                status="confirmed",
            )
            messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)
        messenger.post_text(user_id, reply.render("duplicate_refused", lang), reply_to_id=message_id, trace_id=trace_id)

    def _post_duplicate_check(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        invoice_a: Invoice,
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
    ) -> None:
        original_key, scan_key = self._store_pending(trace_id, original_bytes, original_mime, scan_bytes)
        base_payload = self._pending_payload(
            invoice_a, decision, original_key, original_mime, original_filename, scan_key
        )
        confirm_payload = dict(base_payload)
        deny_payload = dict(base_payload)
        options = [
            {
                "label": reply.render("duplicate_check_confirm", lang),
                "action": "confirm_duplicate",
                "payload": confirm_payload,
            },
            {
                "label": reply.render("duplicate_check_deny", lang),
                "action": "not_duplicate",
                "payload": deny_payload,
            },
        ]
        messenger.post_choice(
            user_id, reply.render("duplicate_check_prompt", lang), options, reply_to_id=message_id, trace_id=trace_id
        )

    def _resolve_project_and_create(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        invoice_a: Invoice,
        projects: list[WritableProject],
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
    ) -> None:
        projects_by_id = {project.id: project for project in projects}
        flags = [] if gate.amounts_ok(decision.amounts_consistent) else ["amounts_to_check"]
        target = projects_by_id.get(decision.project_id) if decision.project_id is not None else None
        status = gate.project_status(decision.project_confidence) if target is not None else "needs_review"

        if status == "needs_review":
            if len(projects) == 1:
                self._finalize_create(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    invoice_a=invoice_a,
                    project=projects[0],
                    category=decision.category,
                    status="needs_review",
                    flags=flags,
                    confidence=decision.project_confidence,
                    original_bytes=original_bytes,
                    original_mime=original_mime,
                    original_filename=original_filename,
                    scan_bytes=scan_bytes,
                    offer_correction=False,
                )
                return
            if not projects:
                messenger.post_text(
                    user_id, reply.render("pick_project_none", lang), reply_to_id=message_id, trace_id=trace_id
                )
                return
            self._post_pick_project_all(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=messenger,
                trace_id=trace_id,
                invoice_a=invoice_a,
                decision=decision,
                projects=projects,
                original_bytes=original_bytes,
                original_mime=original_mime,
                original_filename=original_filename,
                scan_bytes=scan_bytes,
            )
            return

        assert target is not None  # status != "needs_review" implies target was resolved above
        self._finalize_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            invoice_a=invoice_a,
            project=target,
            category=decision.category,
            status=status,
            flags=flags,
            confidence=decision.project_confidence,
            original_bytes=original_bytes,
            original_mime=original_mime,
            original_filename=original_filename,
            scan_bytes=scan_bytes,
            offer_correction=(status == "to_confirm"),
            other_projects=[p for p in projects if p.id != target.id],
        )

    def _post_pick_project_all(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        invoice_a: Invoice,
        decision: TicketDecision,
        projects: list[WritableProject],
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
    ) -> None:
        original_key, scan_key = self._store_pending(trace_id, original_bytes, original_mime, scan_bytes)
        base_payload = self._pending_payload(
            invoice_a, decision, original_key, original_mime, original_filename, scan_key
        )
        options = [
            {
                "label": project.name,
                "action": "set_project",
                "payload": {**base_payload, "project_id": str(project.id)},
            }
            for project in projects
        ]
        messenger.post_choice(
            user_id, reply.render("pick_project_prompt", lang), options, reply_to_id=message_id, trace_id=trace_id
        )

    def _finalize_create(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        invoice_a: Invoice,
        project: WritableProject,
        category: str,
        status: str,
        flags: list[str],
        confidence: float,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
        offer_correction: bool,
        other_projects: Optional[list[WritableProject]] = None,
    ) -> None:
        response = self._create_and_attach(
            user_id=user_id,
            project_id=project.id,
            invoice_a=invoice_a,
            category=category,
            status=status,
            flags=flags,
            confidence=confidence,
            original_bytes=original_bytes,
            original_mime=original_mime,
            original_filename=original_filename,
            scan_bytes=scan_bytes,
            trace_id=trace_id,
        )
        messenger.post_text(
            user_id,
            reply.render("invoice_created", lang, number=response.invoice_number, project=project.name),
            reply_to_id=message_id,
            trace_id=trace_id,
        )
        if "amounts_to_check" in flags:
            messenger.post_text(
                user_id, reply.render("amounts_to_check", lang), reply_to_id=message_id, trace_id=trace_id
            )
        card = self._invoice_card(
            invoice_id=UUID(response.id),
            invoice_number=response.invoice_number,
            project_name=project.name,
            merchant=response.recipient_name,
            total_ttc=response.total_amount,
            issue_date=response.issue_date,
            status=status,
        )
        messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)
        if offer_correction and other_projects:
            options = [
                {
                    "label": candidate.name,
                    "action": "set_project",
                    "payload": {"invoice_id": response.id, "project_id": str(candidate.id)},
                }
                for candidate in other_projects[:_TOP_PROJECTS_FOR_CORRECTION]
            ]
            messenger.post_choice(
                user_id, reply.render("pick_project_prompt", lang), options, reply_to_id=message_id, trace_id=trace_id
            )

    def _create_and_attach(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        invoice_a: Invoice,
        category: str,
        status: str,
        flags: list[str],
        confidence: float,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: bytes,
        trace_id: str,
    ) -> InvoiceResponse:
        response = self._create_invoice(user_id, project_id, invoice_a, trace_id)
        invoice_id = UUID(response.id)
        original_attachment = self._upload_attachment_usecase.execute(
            invoice_id=invoice_id,
            filename=original_filename,
            mime_type=original_mime,
            size_bytes=len(original_bytes),
            fileobj=io.BytesIO(original_bytes),
            uploaded_by=user_id,
        )
        scan_attachment = self._upload_attachment_usecase.execute(
            invoice_id=invoice_id,
            filename=f"scan-{response.invoice_number}.pdf",
            mime_type="application/pdf",
            size_bytes=len(scan_bytes),
            fileobj=io.BytesIO(scan_bytes),
            uploaded_by=user_id,
        )
        self._import_repo.add_invoice_import(
            invoice_id=invoice_id,
            status=status,
            source="ticket",
            ai_confidence=confidence,
            category=category,
            flags=flags,
            original_attachment_id=original_attachment.id,
            scan_attachment_id=scan_attachment.id,
            trace_id=trace_id,
        )
        return response

    def _create_invoice(self, user_id: UUID, project_id: UUID, invoice_a: Invoice, trace_id: str) -> InvoiceResponse:
        issue_date = _parse_date(invoice_a.date) or date.today()
        notes = f"Importé par l'assistant (trace {trace_id})"
        items = _build_line_items(invoice_a)
        request = CreateInvoiceRequest(
            project_id=project_id,
            created_by=user_id,
            type=InvoiceType.MATERIALS_SERVICES,
            issue_date=issue_date,
            recipient_name=invoice_a.merchant,
            recipient_address=invoice_a.store_city,
            items=items,
            notes=notes,
        )
        response = self._create_invoice_usecase.execute(request)
        if abs(response.total_amount - invoice_a.total_ttc) > 0.02:
            # The per-line reconstruction drifted from the extracted TTC total (plan's
            # safety net) — delete and recreate as a single summary line instead.
            self._delete_invoice_usecase.execute(UUID(response.id))
            request.items = _build_single_line_item(invoice_a)
            response = self._create_invoice_usecase.execute(request)
        return response

    # ------------------------------------------------------------------
    # Action taps
    # ------------------------------------------------------------------

    def handle_action(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        action: str,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> bool:
        """Returns True when this feature handled ``action``, False otherwise."""
        if action == "set_project":
            self._action_set_project(user_id, message_id, payload, lang, messenger, trace_id)
            return True
        if action == "confirm_duplicate":
            self._action_confirm_duplicate(user_id, message_id, payload, lang, messenger, trace_id)
            return True
        if action == "not_duplicate":
            self._action_not_duplicate(user_id, message_id, payload, lang, messenger, trace_id)
            return True
        return False

    def _action_set_project(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        project_id = UUID(str(payload["project_id"]))
        if "invoice" in payload:
            self._create_from_pending(user_id, message_id, payload, project_id, lang, messenger, trace_id)
        else:
            invoice_id = UUID(str(payload["invoice_id"]))
            self._move_invoice(user_id, message_id, invoice_id, project_id, lang, messenger, trace_id)

    def _create_from_pending(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        project_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        invoice_a = Invoice.model_validate(payload["invoice"])
        category = str(payload.get("category") or "autre")
        original_bytes = self._fetch_pending(str(payload["original_key"]))
        scan_bytes = self._fetch_pending(str(payload["scan_key"]))
        if original_bytes is None or scan_bytes is None:
            messenger.post_text(user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        project = self._resolve_writable_project(user_id, project_id)
        if project is None:
            messenger.post_text(user_id, reply.render("no_permission", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        confidence = float(payload.get("project_confidence") or 0.0)
        amounts_consistent = float(payload.get("amounts_consistent") or 1.0)
        flags = [] if gate.amounts_ok(amounts_consistent) else ["amounts_to_check"]
        self._finalize_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            invoice_a=invoice_a,
            project=project,
            category=category,
            status="confirmed",
            flags=flags,
            confidence=confidence,
            original_bytes=original_bytes,
            original_mime=str(payload.get("original_mime") or "image/jpeg"),
            original_filename=str(payload.get("original_filename") or "ticket.jpg"),
            scan_bytes=scan_bytes,
            offer_correction=False,
        )
        self._cleanup_pending(str(payload["original_key"]), str(payload["scan_key"]))

    def _move_invoice(
        self,
        user_id: UUID,
        message_id: UUID,
        invoice_id: UUID,
        project_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        existing = self._invoice_repo.find_by_id(invoice_id)
        if existing is None:
            messenger.post_text(user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        new_project = self._resolve_writable_project(user_id, project_id)
        if new_project is None:
            messenger.post_text(user_id, reply.render("no_permission", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        blobs: list[tuple[str, str, bytes]] = []
        for attachment in self._attachment_repo.list_by_invoice(invoice_id):
            stream, _length = self._storage.get_stream(attachment.storage_key)
            blobs.append((attachment.filename, attachment.mime_type, stream.read()))
        prior_import = self._import_repo.find_by_invoice(invoice_id)

        items = [
            {
                "description": item.description,
                "quantity": float(item.quantity),
                "unit_price": float(item.unit_price),
                "vat_rate": float(item.vat_rate),
            }
            for item in existing.items
        ]
        self._delete_invoice_usecase.execute(invoice_id)
        request = CreateInvoiceRequest(
            project_id=project_id,
            created_by=user_id,
            type=existing.type,
            issue_date=existing.issue_date,
            recipient_name=existing.recipient_name,
            recipient_address=existing.recipient_address,
            items=items,
            notes=existing.notes,
        )
        response = self._create_invoice_usecase.execute(request)
        new_invoice_id = UUID(response.id)
        original_attachment_id: Optional[UUID] = None
        scan_attachment_id: Optional[UUID] = None
        for filename, mime_type, data in blobs:
            saved = self._upload_attachment_usecase.execute(
                invoice_id=new_invoice_id,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(data),
                fileobj=io.BytesIO(data),
                uploaded_by=user_id,
            )
            if mime_type == "application/pdf":
                scan_attachment_id = saved.id
            else:
                original_attachment_id = saved.id
        status = prior_import.status if prior_import is not None else "confirmed"
        self._import_repo.add_invoice_import(
            invoice_id=new_invoice_id,
            status=status,
            source="ticket",
            ai_confidence=prior_import.ai_confidence if prior_import is not None else 0.0,
            category=prior_import.category if prior_import is not None else None,
            flags=prior_import.flags if prior_import is not None else [],
            original_attachment_id=original_attachment_id,
            scan_attachment_id=scan_attachment_id,
            trace_id=trace_id,
        )
        messenger.post_text(
            user_id,
            reply.render("invoice_created", lang, number=response.invoice_number, project=new_project.name),
            reply_to_id=message_id,
            trace_id=trace_id,
        )
        card = self._invoice_card(
            invoice_id=new_invoice_id,
            invoice_number=response.invoice_number,
            project_name=new_project.name,
            merchant=response.recipient_name,
            total_ttc=response.total_amount,
            issue_date=response.issue_date,
            status=status,
        )
        messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)

    def _action_confirm_duplicate(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        candidate_id = payload.get("candidate_invoice_id")
        if candidate_id:
            existing = self._invoice_repo.find_by_id(UUID(str(candidate_id)))
            if existing is not None:
                company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
                projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
                project_name = self._project_name(existing.project_id, projects)
                card = self._invoice_card(
                    invoice_id=existing.id,
                    invoice_number=existing.invoice_number,
                    project_name=project_name,
                    merchant=existing.recipient_name,
                    total_ttc=float(existing.total_amount),
                    issue_date=existing.issue_date.isoformat(),
                    status="confirmed",
                )
                messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)
        messenger.post_text(user_id, reply.render("duplicate_refused", lang), reply_to_id=message_id, trace_id=trace_id)
        self._cleanup_pending(str(payload.get("original_key") or ""), str(payload.get("scan_key") or ""))

    def _action_not_duplicate(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        invoice_a = Invoice.model_validate(payload["invoice"])
        original_bytes = self._fetch_pending(str(payload["original_key"]))
        scan_bytes = self._fetch_pending(str(payload["scan_key"]))
        if original_bytes is None or scan_bytes is None:
            messenger.post_text(user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
        projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
        project_id_raw = payload.get("project_id")
        decision = TicketDecision(
            project_id=UUID(str(project_id_raw)) if project_id_raw else None,
            project_confidence=float(payload.get("project_confidence") or 0.0),
            category=str(payload.get("category") or "autre"),
            category_confidence=1.0,
            duplicate_of=None,
            duplicate_confidence=0.0,
            amounts_consistent=float(payload.get("amounts_consistent") or 1.0),
        )
        self._resolve_project_and_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=original_bytes,
            original_mime=str(payload.get("original_mime") or "image/jpeg"),
            original_filename=str(payload.get("original_filename") or "ticket.jpg"),
            scan_bytes=scan_bytes,
        )
        self._cleanup_pending(str(payload["original_key"]), str(payload["scan_key"]))

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _resolve_writable_project(self, user_id: UUID, project_id: UUID) -> Optional[WritableProject]:
        company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
        for project in writable_projects(self._project_repo, self._authz_reader, user_id, company_ids):
            if project.id == project_id:
                return project
        return None

    def _project_name(self, project_id: UUID, projects: list[WritableProject]) -> str:
        for project in projects:
            if project.id == project_id:
                return project.name
        resolved = self._project_repo.find_by_id(project_id)
        return resolved.name if resolved is not None else ""

    def _pending_payload(
        self,
        invoice_a: Invoice,
        decision: TicketDecision,
        original_key: str,
        original_mime: str,
        original_filename: str,
        scan_key: str,
    ) -> dict[str, Any]:
        return {
            "invoice": invoice_a.model_dump(),
            "category": decision.category,
            "project_id": str(decision.project_id) if decision.project_id else None,
            "project_confidence": decision.project_confidence,
            "amounts_consistent": decision.amounts_consistent,
            "candidate_invoice_id": str(decision.duplicate_of) if decision.duplicate_of else None,
            "original_key": original_key,
            "original_mime": original_mime,
            "original_filename": original_filename,
            "scan_key": scan_key,
        }

    def _store_pending(
        self, trace_id: str, original_bytes: bytes, original_mime: str, scan_bytes: bytes
    ) -> tuple[str, str]:
        prefix = f"assistant/pending/{trace_id}-{uuid4().hex[:8]}"
        original_key = f"{prefix}/original"
        scan_key = f"{prefix}/scan.pdf"
        self._storage.put(original_key, io.BytesIO(original_bytes), content_type=original_mime)
        self._storage.put(scan_key, io.BytesIO(scan_bytes), content_type="application/pdf")
        return original_key, scan_key

    def _fetch_pending(self, key: str) -> Optional[bytes]:
        try:
            stream, _length = self._storage.get_stream(key)
            return stream.read()
        except Exception:
            logger.warning("assistant.ticket: failed to read pending storage key %s", key)
            return None

    def _cleanup_pending(self, original_key: str, scan_key: str) -> None:
        for key in (original_key, scan_key):
            if not key:
                continue
            try:
                self._storage.delete(key)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _invoice_card(
        self,
        *,
        invoice_id: UUID,
        invoice_number: str,
        project_name: str,
        merchant: str,
        total_ttc: float,
        issue_date: str,
        status: str,
    ) -> dict[str, Any]:
        return {
            "kind": "invoice",
            "invoice_id": str(invoice_id),
            "invoice_number": invoice_number,
            "title": merchant,
            "subtitle": f"{project_name} · {issue_date} · {total_ttc}€",
            "total_ttc": total_ttc,
            "badge": status,
        }
