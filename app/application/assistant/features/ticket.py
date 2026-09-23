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
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from app.application.assistant import gate, reply
from app.application.assistant.decide import TicketDecision, decide_ticket
from app.application.assistant.exceptions import AssistantError, LlmOutputError, LlmUnavailableError
from app.application.assistant.extract import extract_invoice, is_readable, pdf_to_images
from app.application.assistant.features._photos import read_photo_bytes
from app.application.assistant.import_ports import InvoiceImportRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, Invoice
from app.application.assistant.ports import (
    ChoiceQuestion,
    DecisionPort,
    ImageGenPort,
    MessagePosterPort,
    NoulQuestion,
    VisionLlmPort,
)
from app.application.assistant.scanify import scanify, to_pdf
from app.application.assistant.scope import channel_company_ids
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
from app.application.invoice.upload_attachment import (
    _MAGIC_PEEK_BYTES,
    ALLOWED_MIME_TYPES,
    UnsupportedFileTypeError,
    UploadAttachmentUseCase,
    _matches_magic,
)
from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.invoice import Invoice as InvoiceEntity, InvoiceType
from app.domain.time import business_today

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
#: How many candidates the "which chantier?" choice offers when S3's project confidence
#: lands in the `to_confirm` band (gate.PROJECT_ASK_LOW <= confidence < PROJECT_CONFIRMED).
_TOP_PROJECT_CANDIDATES = 2

#: Every pending object this feature ever writes lives under this prefix — `_fetch_pending`/
#: `_cleanup_pending` refuse any key outside it (defense in depth against a storage key
#: from elsewhere in the bucket ever being read/deleted through this path).
_PENDING_PREFIX = "assistant/pending/"


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


#: Fallback rate used only when nothing else is available to anchor on — the receipt
#: gave neither an HT/TVA pair nor exactly one tva_rate. Flagged for review by every
#: caller of `_line_vat_rate` since a flat 20% guess is frequently wrong on BTP
#: receipts, which mix 5.5/10/20.
_DEFAULT_VAT_RATE = 20.0

#: The only rates French VAT actually takes. `total_tva / total_ht` is an OCR-derived
#: ratio, not a value read straight off the receipt — an OCR misread of either total
#: (e.g. `total_ht` misread as a unit price) can turn it into something like 1100%,
#: which `InvoiceItem.__post_init__` then rejects outright (`vat_rate must be between 0
#: and 100`), losing the whole receipt instead of letting a human fix one field.
_FRENCH_VAT_RATES = (0.0, 2.1, 5.5, 10.0, 20.0)
#: How far a derived ratio may drift from its nearest French rate and still be trusted.
_FRENCH_VAT_RATE_TOLERANCE = 0.5


def _snap_to_french_vat_rate(rate: float) -> Optional[float]:
    """The nearest `_FRENCH_VAT_RATES` entry, when `rate` is within
    `_FRENCH_VAT_RATE_TOLERANCE` points of it — otherwise `None`, meaning the ratio is
    implausible and must not be trusted as-is."""
    nearest = min(_FRENCH_VAT_RATES, key=lambda candidate: abs(candidate - rate))
    return nearest if abs(nearest - rate) <= _FRENCH_VAT_RATE_TOLERANCE else None


def _line_vat_rate(invoice: Invoice) -> tuple[float, bool]:
    """Best-guess VAT rate for the whole ticket, and whether that guess is reliable.

    Priority:
      1. total_tva / total_ht, when both are present AND the ratio snaps to a real
         French rate (within `_FRENCH_VAT_RATE_TOLERANCE`) — an implausible ratio falls
         through to priority 2/3 instead of ever being trusted verbatim.
      2. the receipt's own tva_rates, when there is exactly one. A rate under 1 is a
         fraction, not a percentage (an OCR/Jev misread — e.g. 0.2 meaning 20%).
      3. `_DEFAULT_VAT_RATE`, flagged unreliable — the caller adds `amounts_to_check`.

    More than one distinct `tva_rates` entry (a mixed-rate receipt, e.g. 5.5% and 20%
    lines) always comes back unreliable even when priority 1 lands on a real French
    rate: a single blended percentage still cannot be trusted for the per-line
    reconstruction below.
    """
    mixed_rates = len(invoice.tva_rates) > 1
    if invoice.total_ht is not None and invoice.total_ht > 0 and invoice.total_tva is not None:
        snapped = _snap_to_french_vat_rate((invoice.total_tva / invoice.total_ht) * 100.0)
        if snapped is not None:
            return snapped, not mixed_rates
    if len(invoice.tva_rates) == 1:
        rate = invoice.tva_rates[0]
        return (rate * 100.0 if rate < 1 else rate), True
    return _DEFAULT_VAT_RATE, False


def _build_line_items(invoice: Invoice) -> tuple[list[dict[str, Any]], bool]:
    """Per-line items when every line has a total and no line quantity is negative, else
    one summary line. A negative quantity (e.g. a "RETOUR" line) makes
    `CreateInvoiceUseCase` reject the whole invoice — fall back to the single summary
    line instead of losing the whole receipt over one returned item.

    Returns (items, needs_review) — `needs_review` is True when the VAT rate applied is
    only the last-resort default, never a per-line judgement.
    """
    has_totals = bool(invoice.lines) and all(line.total_ttc is not None for line in invoice.lines)
    no_negative_qty = all(line.qty is None or line.qty >= 0 for line in invoice.lines)
    if has_totals and no_negative_qty:
        vat_rate, reliable = _line_vat_rate(invoice)
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
        return items, not reliable
    return _build_single_line_item(invoice)


def _build_single_line_item(invoice: Invoice) -> tuple[list[dict[str, Any]], bool]:
    vat_rate, reliable = _line_vat_rate(invoice)
    # Always anchor on total_ttc — total_ht is never trusted as the unit price on its
    # own: an OCR misread there would otherwise silently produce the wrong invoice
    # total instead of letting the drift recheck in `_create_invoice` catch it.
    unit_price_ht = invoice.total_ttc / (1 + vat_rate / 100.0)
    label = f"Ticket {invoice.merchant} {invoice.date}".strip() if invoice.date else f"Ticket {invoice.merchant}"
    return [{"description": label, "quantity": 1, "unit_price": unit_price_ht, "vat_rate": vat_rate}], not reliable


def _top_candidate_projects(
    target: WritableProject, projects: list[WritableProject], decision: TicketDecision
) -> list[WritableProject]:
    """``target`` (S3's own pick) plus, when known, the next most likely project from
    Jev's own ``project`` choice probabilities — at most `_TOP_PROJECT_CANDIDATES`."""
    ordered = [target]
    ranked = sorted(decision.project_probabilities.items(), key=lambda item: item[1], reverse=True)
    for project_id_str, _probability in ranked:
        if len(ordered) >= _TOP_PROJECT_CANDIDATES:
            break
        try:
            candidate_id = UUID(project_id_str)
        except ValueError:
            continue
        if candidate_id == target.id:
            continue
        candidate = next((p for p in projects if p.id == candidate_id), None)
        if candidate is not None:
            ordered.append(candidate)
    return ordered


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

    def run(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        photo = read_photo_bytes(self._messages, self._storage, message_id, user_id)
        if photo is None:
            messenger.post_text(
                user_id,
                reply.render("photo_unreadable", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        photo_bytes, photo_filename, photo_mime = photo

        try:
            invoice_a = extract_invoice(self._vision, [photo_bytes])
        except LlmUnavailableError:
            messenger.post_text(
                user_id,
                reply.render("provider_unavailable", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "error"
        except LlmOutputError:
            messenger.post_text(
                user_id,
                reply.render("retake_photo", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        if not is_readable(invoice_a):
            messenger.post_text(
                user_id,
                reply.render("retake_photo", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"

        company_ids = channel_company_ids(scope, user_id, self._company_access, self._authz_reader)
        projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
        if not projects:
            # Nowhere to attach or create an invoice — the answer is always
            # `pick_project_none` (`_resolve_project_and_create`'s own empty-projects
            # branch would reach the same reply anyway). Return here instead of paying
            # for the genai/OpenCV scan and the S3 Jev call first.
            messenger.post_text(
                user_id,
                reply.render("pick_project_none", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        today = business_today()
        ticket_day = _parse_date(invoice_a.date) or today

        candidates = match_candidates(self._invoice_repo, self._import_repo, projects, invoice_a, ticket_day)
        if candidates:
            picked, attach_confidence = self._decide_attach(candidates, invoice_a)
            if picked is not None and gate.attach_existing_allowed(attach_confidence):
                project, existing = picked
                scan_pdf, _mode = self._make_scan(invoice_a, photo_bytes)
                return self._attach_to_existing(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    scope=scope,
                    project=project,
                    existing=existing,
                    confidence=attach_confidence,
                    photo_bytes=photo_bytes,
                    photo_mime=photo_mime,
                    photo_filename=photo_filename,
                    scan_pdf=scan_pdf,
                )

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

        # A rejected duplicate never creates or attaches anything below (`_apply_gate`'s
        # own `_post_duplicate_refused` branch) — skip the paid scan entirely instead of
        # generating one just to discard it.
        is_rejected_duplicate = (
            decision.duplicate_of is not None and gate.duplicate_status(decision.duplicate_confidence) == "reject"
        )
        scan_pdf = None if is_rejected_duplicate else self._make_scan(invoice_a, photo_bytes)[0]

        return self._apply_gate(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=photo_bytes,
            original_mime=photo_mime,
            original_filename=photo_filename,
            scan_bytes=scan_pdf,
        )

    # ------------------------------------------------------------------
    # Entry point — feature B's downloaded PDF (shares S2-S4 with run())
    # ------------------------------------------------------------------

    def run_bytes(
        self,
        *,
        user_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        data: bytes,
        content_type: str,
        chat_hint: Optional[str],
        source: str,
        reply_to_id: Optional[UUID] = None,
    ) -> str:
        """``InvoiceFetchFeature.on_result``'s "done" path: a browser-downloaded PDF is
        already a clean document — no scan step (plan: "the downloaded PDF is already
        clean"). Reuses ``run()``'s exact S2-S4 pipeline; the PDF itself becomes the
        invoice's ``original`` attachment (mime ``application/pdf``) and no second
        (scan) attachment is created. ``chat_hint`` (the router's ``project_hint``) is
        threaded into the S3 state so Jev sees it (plan section 3's priority order).
        """
        try:
            images = pdf_to_images(data) if content_type == "application/pdf" else [data]
            invoice_a = extract_invoice(self._vision, images)
        except LlmUnavailableError:
            messenger.post_text(
                user_id,
                reply.render("provider_unavailable", lang),
                reply_to_id=reply_to_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "error"
        except LlmOutputError:
            messenger.post_text(
                user_id,
                reply.render("fetch_extract_failed", lang),
                reply_to_id=reply_to_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        if not is_readable(invoice_a):
            messenger.post_text(
                user_id,
                reply.render("fetch_extract_failed", lang),
                reply_to_id=reply_to_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"

        filename = "facture.pdf" if content_type == "application/pdf" else "facture"
        company_ids = channel_company_ids(scope, user_id, self._company_access, self._authz_reader)
        projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
        today = business_today()
        ticket_day = _parse_date(invoice_a.date) or today

        candidates = match_candidates(self._invoice_repo, self._import_repo, projects, invoice_a, ticket_day)
        if candidates:
            picked, attach_confidence = self._decide_attach(candidates, invoice_a)
            if picked is not None and gate.attach_existing_allowed(attach_confidence):
                project, existing = picked
                return self._attach_to_existing(
                    user_id=user_id,
                    message_id=reply_to_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    scope=scope,
                    project=project,
                    existing=existing,
                    confidence=attach_confidence,
                    photo_bytes=data,
                    photo_mime=content_type,
                    photo_filename=filename,
                    scan_pdf=None,
                    source=source,
                )

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
            invoice_a, projects, workers_by_project, recent_by_project, dup_candidates, sane, chat_hint=chat_hint
        )
        decision = decide_ticket(self._decisions, ticket_state, projects, dup_candidates)

        return self._apply_gate(
            user_id=user_id,
            message_id=reply_to_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=data,
            original_mime=content_type,
            original_filename=filename,
            scan_bytes=None,
            source=source,
        )

    # ------------------------------------------------------------------
    # C2 — scan generation
    # ------------------------------------------------------------------

    def _make_scan(self, invoice_a: Invoice, photo_bytes: bytes) -> tuple[Optional[bytes], str]:
        """Never raises — a scan failure must never abort the ticket import. genai
        (when enabled) falls back to OpenCV on any exception; OpenCV itself falls back
        to no scan at all (``scan=None``) rather than losing the whole receipt over
        `img2pdf`/`scanify`/a Gemini SDK error."""
        if self._scan_mode == "genai":
            try:
                generated = self._try_genai_scan(invoice_a, photo_bytes)
            except Exception:
                logger.exception("assistant.ticket: genai scan generation crashed, falling back to OpenCV")
                generated = None
            if generated is not None:
                try:
                    return to_pdf(generated), "genai"
                except Exception:
                    logger.exception("assistant.ticket: genai scan to_pdf failed, falling back to OpenCV")
        try:
            processed = scanify(photo_bytes, self._vision)
            return to_pdf(processed), "opencv"
        except Exception:
            logger.exception("assistant.ticket: OpenCV scan fallback failed, continuing without a scan")
            return None, "none"

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
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        project: WritableProject,
        existing: InvoiceEntity,
        confidence: float,
        photo_bytes: bytes,
        photo_mime: str,
        photo_filename: str,
        scan_pdf: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        original_attachment = self._upload_attachment_usecase.execute(
            invoice_id=existing.id,
            filename=photo_filename,
            mime_type=photo_mime,
            size_bytes=len(photo_bytes),
            fileobj=io.BytesIO(photo_bytes),
            uploaded_by=user_id,
        )
        scan_attachment_id: Optional[UUID] = None
        if scan_pdf is not None:
            scan_attachment = self._upload_attachment_usecase.execute(
                invoice_id=existing.id,
                filename=f"scan-{existing.invoice_number}.pdf",
                mime_type="application/pdf",
                size_bytes=len(scan_pdf),
                fileobj=io.BytesIO(scan_pdf),
                uploaded_by=user_id,
            )
            scan_attachment_id = scan_attachment.id
        existing_import = self._import_repo.find_by_invoice(existing.id)
        status = existing_import.status if existing_import is not None else "confirmed"
        self._import_repo.add_invoice_import(
            invoice_id=existing.id,
            status=status,
            source=source,
            ai_confidence=confidence,
            original_attachment_id=original_attachment.id,
            scan_attachment_id=scan_attachment_id,
            trace_id=trace_id,
        )
        messenger.post_text(
            user_id,
            reply.render("invoice_attached", lang, number=existing.invoice_number),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        card = self._invoice_card(
            invoice_id=existing.id,
            project_id=existing.project_id,
            invoice_number=existing.invoice_number,
            project_name=project.name,
            merchant=existing.recipient_name,
            total_ttc=float(existing.total_amount),
            issue_date=existing.issue_date.isoformat(),
            status=status,
        )
        messenger.post_card(
            user_id,
            **card,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "attached"

    # ------------------------------------------------------------------
    # S4 — gate: duplicate check, then project assignment
    # ------------------------------------------------------------------

    def _apply_gate(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        invoice_a: Invoice,
        projects: list[WritableProject],
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        if decision.duplicate_of is not None:
            dup_status = gate.duplicate_status(decision.duplicate_confidence)
            if dup_status == "reject":
                return self._post_duplicate_refused(
                    user_id, message_id, lang, messenger, trace_id, decision.duplicate_of, projects, scope=scope
                )
            if dup_status == "ask":
                return self._post_duplicate_check(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    scope=scope,
                    invoice_a=invoice_a,
                    decision=decision,
                    original_bytes=original_bytes,
                    original_mime=original_mime,
                    original_filename=original_filename,
                    scan_bytes=scan_bytes,
                    source=source,
                )
        return self._resolve_project_and_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=original_bytes,
            original_mime=original_mime,
            original_filename=original_filename,
            scan_bytes=scan_bytes,
            source=source,
        )

    def _post_duplicate_refused(
        self,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        duplicate_of: UUID,
        projects: list[WritableProject],
        scope: ChannelScope,
    ) -> str:
        existing = self._invoice_repo.find_by_id(duplicate_of)
        if existing is not None:
            project_name = self._project_name(existing.project_id, projects)
            card = self._invoice_card(
                invoice_id=existing.id,
                project_id=existing.project_id,
                invoice_number=existing.invoice_number,
                project_name=project_name,
                merchant=existing.recipient_name,
                total_ttc=float(existing.total_amount),
                issue_date=existing.issue_date.isoformat(),
                status="confirmed",
            )
            messenger.post_card(
                user_id,
                **card,
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
        messenger.post_text(
            user_id,
            reply.render("duplicate_refused", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "refused"

    def _post_duplicate_check(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        invoice_a: Invoice,
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        original_key, scan_key = self._store_pending(trace_id, original_bytes, original_mime, scan_bytes)
        base_payload = self._pending_payload(
            invoice_a, decision, original_key, original_mime, original_filename, scan_key, source
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
            user_id,
            reply.render("duplicate_check_prompt", lang),
            options,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "asked"

    def _resolve_project_and_create(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        invoice_a: Invoice,
        projects: list[WritableProject],
        decision: TicketDecision,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        projects_by_id = {project.id: project for project in projects}
        flags = [] if gate.amounts_ok(decision.amounts_consistent) else ["amounts_to_check"]
        target = projects_by_id.get(decision.project_id) if decision.project_id is not None else None
        status = gate.project_status(decision.project_confidence) if target is not None else "needs_review"

        if status == "needs_review":
            if len(projects) == 1:
                return self._finalize_create(
                    user_id=user_id,
                    message_id=message_id,
                    lang=lang,
                    messenger=messenger,
                    trace_id=trace_id,
                    scope=scope,
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
                    source=source,
                )
            if not projects:
                messenger.post_text(
                    user_id,
                    reply.render("pick_project_none", lang),
                    reply_to_id=message_id,
                    trace_id=trace_id,
                    channel=scope.channel,
                    scope=scope,
                )
                return "refused"
            return self._post_pick_project_all(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=messenger,
                trace_id=trace_id,
                scope=scope,
                invoice_a=invoice_a,
                decision=decision,
                projects=projects,
                original_bytes=original_bytes,
                original_mime=original_mime,
                original_filename=original_filename,
                scan_bytes=scan_bytes,
                source=source,
            )

        if target is None:  # status != "needs_review" only when target was resolved above
            raise AssistantError("_resolve_project_and_create: to_confirm/confirmed status without a target.")

        # D13: below full confidence, don't create yet — ask (top-2 candidates) and
        # create ON TAP from the pending state, exactly like the needs_review/multi-
        # project branch above. A single writable project always creates directly
        # (there is nothing to ask about). This replaces the old "create now, then
        # offer a correction button that deletes+recreates the invoice" flow (decision
        # D13 / review finding C2): that button silently dropped payment/refund/
        # highlight/worker links and could destroy an invoice non-atomically.
        if status == "to_confirm" and len(projects) > 1:
            return self._post_pick_project_all(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=messenger,
                trace_id=trace_id,
                scope=scope,
                invoice_a=invoice_a,
                decision=decision,
                projects=_top_candidate_projects(target, projects, decision),
                original_bytes=original_bytes,
                original_mime=original_mime,
                original_filename=original_filename,
                scan_bytes=scan_bytes,
                source=source,
            )

        return self._finalize_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
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
            source=source,
        )

    def _post_pick_project_all(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        invoice_a: Invoice,
        decision: TicketDecision,
        projects: list[WritableProject],
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        original_key, scan_key = self._store_pending(trace_id, original_bytes, original_mime, scan_bytes)
        base_payload = self._pending_payload(
            invoice_a, decision, original_key, original_mime, original_filename, scan_key, source
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
            user_id,
            reply.render("pick_project_prompt", lang),
            options,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "asked"

    def _finalize_create(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        invoice_a: Invoice,
        project: WritableProject,
        category: str,
        status: str,
        flags: list[str],
        confidence: float,
        original_bytes: bytes,
        original_mime: str,
        original_filename: str,
        scan_bytes: Optional[bytes],
        source: str = "ticket",
    ) -> str:
        response, final_flags = self._create_and_attach(
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
            scope=scope,
            source=source,
            messenger=messenger,
        )
        messenger.post_text(
            user_id,
            reply.render("invoice_created", lang, number=response.invoice_number, project=project.name),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        if "amounts_to_check" in final_flags:
            messenger.post_text(
                user_id,
                reply.render("amounts_to_check", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
        card = self._invoice_card(
            invoice_id=UUID(response.id),
            project_id=project.id,
            invoice_number=response.invoice_number,
            project_name=project.name,
            merchant=response.recipient_name,
            total_ttc=response.total_amount,
            issue_date=response.issue_date,
            status=status,
        )
        messenger.post_card(
            user_id,
            **card,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "created"

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
        scan_bytes: Optional[bytes],
        trace_id: str,
        scope: ChannelScope,
        messenger: AssistantMessenger,
        source: str = "ticket",
    ) -> tuple[InvoiceResponse, list[str]]:
        # Validate the attachment BEFORE creating the invoice — chat only checks the
        # declared MIME type; `UploadAttachmentUseCase`'s own magic-byte check
        # previously only ran AFTER the invoice already existed, leaving an orphan on a
        # mismatch (a PNG sent as `image/jpeg`, for example).
        if original_mime not in ALLOWED_MIME_TYPES or not _matches_magic(
            original_mime, original_bytes[:_MAGIC_PEEK_BYTES]
        ):
            raise UnsupportedFileTypeError(f"File contents do not match declared type '{original_mime}'")

        response, needs_review = self._create_invoice(user_id, project_id, invoice_a, trace_id)
        invoice_id = UUID(response.id)
        final_flags = list(flags)
        if needs_review and "amounts_to_check" not in final_flags:
            final_flags.append("amounts_to_check")
        try:
            original_attachment = self._upload_attachment_usecase.execute(
                invoice_id=invoice_id,
                filename=original_filename,
                mime_type=original_mime,
                size_bytes=len(original_bytes),
                fileobj=io.BytesIO(original_bytes),
                uploaded_by=user_id,
            )
            scan_attachment_id: Optional[UUID] = None
            if scan_bytes is not None:
                scan_attachment = self._upload_attachment_usecase.execute(
                    invoice_id=invoice_id,
                    filename=f"scan-{response.invoice_number}.pdf",
                    mime_type="application/pdf",
                    size_bytes=len(scan_bytes),
                    fileobj=io.BytesIO(scan_bytes),
                    uploaded_by=user_id,
                )
                scan_attachment_id = scan_attachment.id
            self._import_repo.add_invoice_import(
                invoice_id=invoice_id,
                status=status,
                source=source,
                ai_confidence=confidence,
                category=category,
                flags=final_flags,
                original_attachment_id=original_attachment.id,
                scan_attachment_id=scan_attachment_id,
                trace_id=trace_id,
            )
        except Exception:
            # Anything after create fails (the S3 put, `add_invoice_import`'s DB write,
            # a magic-byte mismatch the pre-check above missed) leaves an invoice with no
            # attachment and no import row — delete it; nothing else references it yet,
            # so this is the only place anything ever links to it.
            logger.exception("assistant.ticket: post-create failure for invoice %s, deleting the orphan", invoice_id)
            # A DB write in the try block (`add_invoice_import`, an attachment row) can
            # be exactly what raised — the delete below runs on the same session and
            # must start from a clean transaction or it raises too, leaving the orphan
            # invoice (already committed by `CreateInvoiceUseCase`) behind for good.
            messenger.rollback()
            try:
                self._delete_invoice_usecase.execute(invoice_id)
            except Exception:
                logger.exception("assistant.ticket: failed to delete orphaned invoice %s", invoice_id)
            raise
        return response, final_flags

    #: Tolerance the reconstructed invoice's total is allowed to drift from the
    #: receipt's extracted TTC before the single-line fallback is triggered, and again
    #: afterwards before flagging the write for review — a rebuild that still doesn't
    #: match a cent-precision original total is gated for a human, not silently
    #: committed.
    _TOTAL_DRIFT_TOLERANCE = 0.01
    #: A ticket dated further back or further forward than this is almost certainly an
    #: OCR misread (e.g. "2062") — replaced by today and flagged.
    _ISSUE_DATE_PAST_WINDOW = timedelta(days=730)
    _ISSUE_DATE_FUTURE_WINDOW = timedelta(days=7)

    def _sane_issue_date(self, raw_date: Optional[str], today: date) -> tuple[date, bool]:
        parsed = _parse_date(raw_date)
        if parsed is None:
            return today, False
        if parsed < today - self._ISSUE_DATE_PAST_WINDOW or parsed > today + self._ISSUE_DATE_FUTURE_WINDOW:
            return today, True
        return parsed, False

    def _create_invoice(
        self,
        user_id: UUID,
        project_id: UUID,
        invoice_a: Invoice,
        trace_id: str,
    ) -> tuple[InvoiceResponse, bool]:
        today = business_today()
        issue_date, date_out_of_range = self._sane_issue_date(invoice_a.date, today)
        notes = f"Importé par l'assistant (trace {trace_id})"
        items, needs_review = _build_line_items(invoice_a)
        needs_review = needs_review or date_out_of_range
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
        if abs(response.total_amount - invoice_a.total_ttc) > self._TOTAL_DRIFT_TOLERANCE:
            # The per-line reconstruction drifted from the extracted TTC total (plan's
            # safety net) — delete and recreate as a single summary line instead.
            self._delete_invoice_usecase.execute(UUID(response.id))
            items, single_line_needs_review = _build_single_line_item(invoice_a)
            needs_review = needs_review or single_line_needs_review
            request.items = items
            response = self._create_invoice_usecase.execute(request)
            if abs(response.total_amount - invoice_a.total_ttc) > self._TOTAL_DRIFT_TOLERANCE:
                # Still off after the fallback — flag it instead of silently keeping a
                # wrong total.
                needs_review = True
        return response, needs_review

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
        scope: ChannelScope,
    ) -> bool:
        """Returns True when this feature handled ``action``, False otherwise."""
        if action == "set_project":
            self._action_set_project(user_id, message_id, payload, lang, messenger, trace_id, scope=scope)
            return True
        if action == "confirm_duplicate":
            self._action_confirm_duplicate(user_id, message_id, payload, lang, messenger, trace_id, scope=scope)
            return True
        if action == "not_duplicate":
            self._action_not_duplicate(user_id, message_id, payload, lang, messenger, trace_id, scope=scope)
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
        scope: ChannelScope,
    ) -> None:
        # "Move an already-created invoice to another project" (delete + recreate) was
        # removed entirely (decision D13): it was non-atomic, silently dropped payment/
        # refund/highlight/worker links, and `invoices.refunds_invoice_id`/
        # `applied_to_invoice_id` are ON DELETE SET NULL so it silently broke avoir/
        # refund back-references. `set_project` now only ever creates from pending state.
        if "invoice" not in payload:
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        project_id = UUID(str(payload["project_id"]))
        self._create_from_pending(user_id, message_id, payload, project_id, lang, messenger, trace_id, scope=scope)

    def _create_from_pending(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        project_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> None:
        invoice_a = Invoice.model_validate(payload["invoice"])
        category = str(payload.get("category") or "autre")
        original_bytes = self._fetch_pending(str(payload["original_key"]))
        scan_key = payload.get("scan_key")
        scan_bytes = self._fetch_pending(str(scan_key)) if scan_key else None
        if original_bytes is None or (scan_key and scan_bytes is None):
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        project = self._resolve_writable_project(user_id, project_id, scope)
        if project is None:
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        confidence = float(payload.get("project_confidence") or 0.0)
        raw_amounts_consistent = payload.get("amounts_consistent")
        # A Jev noul of exactly 0.0 must not be dropped by `or 1.0` — only a genuinely
        # missing key defaults to "consistent".
        amounts_consistent = float(raw_amounts_consistent) if raw_amounts_consistent is not None else 1.0
        flags = [] if gate.amounts_ok(amounts_consistent) else ["amounts_to_check"]
        self._finalize_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
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
            source=str(payload.get("source") or "ticket"),
        )
        self._cleanup_pending(str(payload["original_key"]), str(scan_key or ""))

    def _action_confirm_duplicate(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> None:
        candidate_id = payload.get("candidate_invoice_id")
        if candidate_id:
            existing = self._invoice_repo.find_by_id(UUID(str(candidate_id)))
            # Only ever show a card for an invoice on a project the caller can see —
            # closes the "any invoice's details disclosed" IDOR even if a forged
            # `candidate_invoice_id` ever reached this far (defense in depth: the
            # SubmitAssistantActionUseCase fix already stops a forged one from arriving).
            writable = (
                self._resolve_writable_project(user_id, existing.project_id, scope) if existing is not None else None
            )
            if existing is not None and writable is not None:
                card = self._invoice_card(
                    invoice_id=existing.id,
                    project_id=existing.project_id,
                    invoice_number=existing.invoice_number,
                    project_name=writable.name,
                    merchant=existing.recipient_name,
                    total_ttc=float(existing.total_amount),
                    issue_date=existing.issue_date.isoformat(),
                    status="confirmed",
                )
                messenger.post_card(
                    user_id,
                    **card,
                    reply_to_id=message_id,
                    trace_id=trace_id,
                    channel=scope.channel,
                    scope=scope,
                )
        messenger.post_text(
            user_id,
            reply.render("duplicate_refused", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        self._cleanup_pending(str(payload.get("original_key") or ""), str(payload.get("scan_key") or ""))

    def _action_not_duplicate(
        self,
        user_id: UUID,
        message_id: UUID,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> None:
        invoice_a = Invoice.model_validate(payload["invoice"])
        original_bytes = self._fetch_pending(str(payload["original_key"]))
        scan_key = payload.get("scan_key")
        scan_bytes = self._fetch_pending(str(scan_key)) if scan_key else None
        if original_bytes is None or (scan_key and scan_bytes is None):
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        company_ids = channel_company_ids(scope, user_id, self._company_access, self._authz_reader)
        projects = writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
        project_id_raw = payload.get("project_id")
        decision = TicketDecision(
            project_id=UUID(str(project_id_raw)) if project_id_raw else None,
            project_confidence=float(payload.get("project_confidence") or 0.0),
            project_probabilities={},
            category=str(payload.get("category") or "autre"),
            category_confidence=1.0,
            duplicate_of=None,
            duplicate_confidence=0.0,
            amounts_consistent=(
                float(payload["amounts_consistent"]) if payload.get("amounts_consistent") is not None else 1.0
            ),
        )
        self._resolve_project_and_create(
            user_id=user_id,
            message_id=message_id,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
            invoice_a=invoice_a,
            projects=projects,
            decision=decision,
            original_bytes=original_bytes,
            original_mime=str(payload.get("original_mime") or "image/jpeg"),
            original_filename=str(payload.get("original_filename") or "ticket.jpg"),
            scan_bytes=scan_bytes,
            source=str(payload.get("source") or "ticket"),
        )
        self._cleanup_pending(str(payload["original_key"]), str(scan_key or ""))

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _resolve_writable_project(
        self, user_id: UUID, project_id: UUID, scope: ChannelScope
    ) -> Optional[WritableProject]:
        company_ids = channel_company_ids(scope, user_id, self._company_access, self._authz_reader)
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
        scan_key: Optional[str],
        source: str = "ticket",
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
            "source": source,
        }

    def _store_pending(
        self, trace_id: str, original_bytes: bytes, original_mime: str, scan_bytes: Optional[bytes]
    ) -> tuple[str, Optional[str]]:
        prefix = f"{_PENDING_PREFIX}{trace_id}-{uuid4().hex[:8]}"
        original_key = f"{prefix}/original"
        self._storage.put(original_key, io.BytesIO(original_bytes), content_type=original_mime)
        if scan_bytes is None:
            return original_key, None
        scan_key = f"{prefix}/scan.pdf"
        self._storage.put(scan_key, io.BytesIO(scan_bytes), content_type="application/pdf")
        return original_key, scan_key

    def _fetch_pending(self, key: str) -> Optional[bytes]:
        # Defense in depth: a pending key only ever comes back from a stored choice
        # option's own payload (see `SubmitAssistantActionUseCase`'s stored-option
        # check), but this still refuses to fetch anything outside the one prefix this
        # feature ever writes to — an attachment/photo storage key can never be read
        # through this path even if a future handler forwarded one by mistake.
        if not key.startswith(_PENDING_PREFIX):
            logger.warning("assistant.ticket: refused to read a non-pending storage key %s", key)
            return None
        try:
            stream, _length = self._storage.get_stream(key)
            return stream.read()
        except Exception:
            logger.warning("assistant.ticket: failed to read pending storage key %s", key)
            return None

    def _cleanup_pending(self, original_key: str, scan_key: str) -> None:
        for key in (original_key, scan_key):
            if not key or not key.startswith(_PENDING_PREFIX):
                continue
            try:
                self._storage.delete(key)
            except Exception:
                pass

    def _invoice_card(
        self,
        *,
        invoice_id: UUID,
        project_id: UUID,
        invoice_number: str,
        project_name: str,
        merchant: str,
        total_ttc: float,
        issue_date: str,
        status: str,
    ) -> dict[str, Any]:
        """Keyword arguments for ``AssistantMessenger.post_card(user_id, **card, ...)`` —
        see that method for the wire contract this is shaped to feed."""
        return {
            "card_type": "invoice",
            "entity_id": invoice_id,
            "project_id": project_id,
            "title": merchant,
            "subtitle": f"{project_name} · {issue_date} · {total_ttc}€",
            "badge": status,
            "extra": {"invoice_number": invoice_number, "total_ttc": total_ttc},
        }
