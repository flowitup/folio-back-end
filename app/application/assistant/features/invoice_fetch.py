"""Feature B — invoice fetch via the browser worker container.

Pipeline (plan section 3/4, "Feature B"):
  B0 chat -> S0 (already routed by the time ``fetch_invoice`` is called) -> one DeepSeek
     call parses the amount/date out of the chat text ("79,54e", "hier", "21/09") ->
     dedupe (same merchant+amount+date within 24h reuses the in-flight job) -> a row is
     inserted into ``assistant_jobs`` (status queued) -> a ``job_status`` message is
     posted and immediately acknowledged ("je m'en occupe").
  B1 the ``ai-browser`` container (``app.infrastructure.browser_worker``) polls
     ``assistant_jobs``, runs the agent, stores the PDF in S3, writes the result and
     enqueues ``process_fetched_invoice`` on the ``assistant`` RQ queue.
  B2 ``on_result`` (this module) reads the job's outcome and finishes the job_status
     message's state machine: done -> hands the PDF to ``TicketFeature.run_bytes`` (the
     shared S2-S4 pipeline, no scan step); not_ready -> requeue with backoff (max 3
     attempts) or fail; not_found -> a choice of the 5 closest recorded purchases;
     blocked -> ask for the ticket photo instead (routes the user to feature C).

Every job_status transition updates the message's payload in place
(``AssistantMessenger.update_job_status``) and pushes through the notifier only when the
transition is terminal (done/failed/not_found/blocked) — plan section 9.3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.features.ticket import TicketFeature
from app.application.assistant.jobs_repo import AssistantJobRecord, AssistantJobRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import MERCHANTS, AmountDate, RouterDecision
from app.application.assistant.ports import MessagePosterPort, VisionLlmPort
from app.application.assistant.state import WritableProject, writable_projects
from app.application.authz.ports import AuthzReaderPort
from app.application.chat.ports import ChatAttachmentStoragePort
from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.invoice.ports import IInvoiceRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.domain.entities.invoice import Invoice as InvoiceEntity, InvoiceType

logger = logging.getLogger(__name__)

# Keep byte-identical across every call — same rule as extract.py's S1 prompt (plan hard
# rule #4). Only amount/date: merchant and project_hint already come out of the S0
# router's own Jev call, never re-derived here.
AMOUNT_DATE_SYSTEM_FR = (
    "Tu extrais le montant TTC et la date d'un achat mentionné dans un message de chat sur un chantier BTP "
    "français. Réponds uniquement avec un JSON aux champs : amount_ttc (nombre avec un point décimal, ex 79.54), "
    'date (YYYY-MM-DD). Résous les expressions relatives ("hier", "avant-hier", "il y a 3 jours") et les '
    "dates courtes (\"21/09\") par rapport à la date d'aujourd'hui fournie dans le message. "
    "Champs absents ou non mentionnés dans le message → null. N'invente rien."
)

#: How many attempts a `not_ready` result gets before the job is marked `failed`.
MAX_ATTEMPTS = 3
#: Backoff before retrying a `not_ready` job.
NOT_READY_BACKOFF = timedelta(hours=2)
#: Dedupe window: a new fetch_invoice request for the same merchant+amount+date reuses
#: whatever job is already active instead of spawning a second one.
DEDUPE_WINDOW = timedelta(hours=24)
#: How many "closest purchase" candidates the not_found choice offers.
NOT_FOUND_CANDIDATES = 5

#: `_closest_purchases` only ever looks this far around the target date (review finding
#: H5) — a legitimate "which purchase is this" match is always close in time; scanning
#: every materials/services invoice ever recorded on every writable project was O(all
#: invoices) per not_found reply.
CLOSEST_PURCHASE_WINDOW = timedelta(days=90)

_DATE_WITH_YEAR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_NO_YEAR_RE = re.compile(r"^(\d{2})-(\d{2})$")
_AMOUNT_CLEAN_RE = re.compile(r"[^0-9.\-]")


def _normalize_amount(value: Any) -> Optional[float]:
    """Defensive post-processing on top of DeepSeek's JSON: accepts a clean float (the
    normal case, already coerced by pydantic) or a leftover string with a comma decimal
    separator/currency suffix ("79,54e", "79,54€") and returns a clean float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().lower()
    text = text.replace(",", ".")
    text = _AMOUNT_CLEAN_RE.sub("", text)
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _normalize_date(value: Optional[str], today: date) -> Optional[date]:
    """Defensive post-processing on DeepSeek's date: a full ISO date is used as-is; a
    bare "MM-DD" (the model did not resolve the year) gets the current year; any result
    landing in the future (a purchase cannot postdate "today") rolls back one year."""
    if not value:
        return None
    text = value.strip()
    parsed: Optional[date] = None
    if _DATE_WITH_YEAR_RE.match(text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None
    else:
        match = _DATE_NO_YEAR_RE.match(text)
        if match is None:
            return None
        month, day = int(match.group(1)), int(match.group(2))
        try:
            parsed = date(today.year, month, day)
        except ValueError:
            return None
    if parsed > today:
        try:
            parsed = parsed.replace(year=parsed.year - 1)
        except ValueError:
            # Feb 29 landing on a non-leap previous year.
            parsed = parsed.replace(month=2, day=28, year=parsed.year - 1)
    return parsed


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _normalize_merchant_text(value: str) -> str:
    """Strips spaces/punctuation so a router merchant key ("leroymerlin") matches a
    human-readable recipient name ("Leroy Merlin") extracted from a real document."""
    return _NON_ALNUM_RE.sub("", value.lower())


def _recipient_matches_merchant(recipient_name: Optional[str], merchant: str) -> bool:
    a = _normalize_merchant_text(recipient_name or "")
    b = _normalize_merchant_text(merchant or "")
    if not a or not b:
        return False
    return b in a or a in b


@dataclass(frozen=True)
class _StatusContext:
    """What ``on_result`` needs to keep updating the same job_status message/lang."""

    lang: str
    reply_to_id: Optional[UUID]


class InvoiceFetchFeature:
    """Implements feature B end to end: ``fetch_invoice()`` (job creation) and
    ``on_result()``/``handle_action()`` (the job's state machine)."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        messages: MessagePosterPort,
        storage: ChatAttachmentStoragePort,
        job_repo: AssistantJobRepositoryPort,
        ticket: TicketFeature,
        company_access: UserCompanyAccessRepositoryPort,
        project_repo: IProjectRepository,
        authz_reader: AuthzReaderPort,
        invoice_repo: IInvoiceRepository,
    ) -> None:
        self._vision = vision
        self._messages = messages
        self._storage = storage
        self._job_repo = job_repo
        self._ticket = ticket
        self._company_access = company_access
        self._project_repo = project_repo
        self._authz_reader = authz_reader
        self._invoice_repo = invoice_repo

    # ------------------------------------------------------------------
    # B0 — job creation
    # ------------------------------------------------------------------

    def fetch_invoice(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        channel: Optional[ChannelRef] = None,
        decision: RouterDecision,
    ) -> str:
        merchant = decision.merchant if decision.merchant in MERCHANTS else None
        if merchant is None:
            messenger.post_text(
                user_id,
                reply.render("fetch_need_merchant", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "asked"

        message = self._messages.find_by_id(message_id)
        text = (message.body or "").strip() if message is not None else ""
        today = date.today()
        user_text = f"Aujourd'hui : {today.isoformat()}. Message : {text}"
        try:
            parsed = self._vision.chat_json(
                system=AMOUNT_DATE_SYSTEM_FR, user_text=user_text, images=[], model_cls=AmountDate
            )
        except LlmOutputError:
            messenger.post_text(
                user_id,
                reply.render("fetch_need_amount", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "asked"

        amount = _normalize_amount(parsed.amount_ttc)
        if amount is None:
            messenger.post_text(
                user_id,
                reply.render("fetch_need_amount", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "asked"
        ticket_date = _normalize_date(parsed.date, today) or today

        since = datetime.now(timezone.utc) - DEDUPE_WINDOW
        amount_decimal = Decimal(str(amount))
        duplicate = self._job_repo.find_duplicate(
            user_id=user_id, merchant=merchant, amount_ttc=amount_decimal, date=ticket_date, since=since
        )
        if duplicate is not None:
            messenger.post_text(
                user_id,
                reply.render("fetch_already_running", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "refused"

        job = self._job_repo.add(
            user_id=user_id,
            merchant=merchant,
            amount_ttc=amount_decimal,
            date=ticket_date,
            project_hint=decision.project_hint,
            lang=lang,
            channel_key=channel.key if channel is not None else None,
        )
        status_message = messenger.post_job_status(
            user_id,
            job_id=str(job.id),
            state="queued",
            text=reply.render("fetch_ack", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=channel,
        )
        self._job_repo.set_status_message(job.id, status_message.id)
        return "replied"

    # ------------------------------------------------------------------
    # B2 — the browser worker's result (`process_fetched_invoice` RQ job)
    # ------------------------------------------------------------------

    def on_result(self, job_id: UUID, *, messenger: AssistantMessenger, trace_id: str) -> None:
        job = self._job_repo.find_by_id(job_id)
        if job is None:
            logger.warning("assistant.fetch_invoice: job %s not found", job_id)
            return
        # Post back into the channel the request actually came from (phase 03's answer
        # to phase 01/02's open question 2) — falls back to `AssistantMessenger`'s own
        # retired-channel default for a job created before this column existed.
        channel: Optional[ChannelRef] = None
        if job.channel_key:
            try:
                channel = ChannelRef.parse(job.channel_key)
            except ValueError:
                logger.warning("assistant.fetch_invoice: job %s has an unparsable channel_key", job.id)
        context = self._status_context(job)
        status = job.status
        if status == "done":
            self._handle_done(job, context, messenger, trace_id, channel=channel)
        elif status == "not_ready":
            self._handle_not_ready(job, context, messenger, trace_id, channel=channel)
        elif status == "blocked":
            self._handle_terminal_once(
                job, context, messenger, state="blocked", template="fetch_blocked", channel=channel
            )
        elif status == "not_found":
            self._handle_not_found(job, context, messenger, trace_id, channel=channel)
        else:  # pragma: no cover - defensive: the worker only ever writes the above
            logger.warning("assistant.fetch_invoice: job %s has unexpected status %s", job_id, status)

    def _handle_terminal_once(
        self,
        job: AssistantJobRecord,
        context: _StatusContext,
        messenger: AssistantMessenger,
        *,
        state: str,
        template: str,
        channel: Optional[ChannelRef] = None,
    ) -> None:
        """A one-shot terminal reply (the ``blocked`` outcome — no retry, no further
        state). Guarded by ``mark_processed`` the same way ``_handle_done`` is (review
        finding NEW-H2): the unprocessed-job reaper (``reap_unprocessed`` in the job
        repository) re-enqueues ``process_fetched_invoice`` for any terminal row whose
        ``processed_at`` never got set — without this guard, a job that already finished
        normally the first time would get its "blocked" reply posted to the user a
        second time 30 minutes later."""
        if not self._job_repo.mark_processed(job.id):
            logger.info("assistant.fetch_invoice: job %s already processed, skipping", job.id)
            return
        self._finish(job, context, messenger, state=state, template=template, channel=channel)

    def _status_context(self, job: AssistantJobRecord) -> _StatusContext:
        original: Optional[ChatMessage] = None
        if job.status_message_id is not None:
            status_message = self._messages.find_by_id(job.status_message_id)
            if status_message is not None and status_message.reply_to_id is not None:
                original = self._messages.find_by_id(status_message.reply_to_id)
        lang_hint = (original.payload or {}).get("lang") if original is not None and original.payload else None
        lang = reply.detect_lang(original.body or "" if original is not None else "", lang_hint)
        return _StatusContext(lang=lang, reply_to_id=job.status_message_id)

    def _handle_done(
        self,
        job: AssistantJobRecord,
        context: _StatusContext,
        messenger: AssistantMessenger,
        trace_id: str,
        channel: Optional[ChannelRef] = None,
    ) -> None:
        if not self._job_repo.mark_processed(job.id):
            # Already processed (a second `process_fetched_invoice` invocation for the
            # same job — RQ retry, manual requeue, a double enqueue after a worker crash
            # between `update_result` and `enqueue`) — skip re-running the create-invoice
            # pipeline entirely rather than writing a duplicate invoice (review finding
            # H3). The user already got their terminal reply the first time.
            logger.info("assistant.fetch_invoice: job %s already processed, skipping", job.id)
            return
        if job.pdf_storage_key is None:
            self._finish(job, context, messenger, state="failed", template="fetch_failed", channel=channel)
            return
        try:
            stream, _length = self._storage.get_stream(job.pdf_storage_key)
            data = stream.read()
        except Exception:
            logger.exception("assistant.fetch_invoice: failed to read PDF for job %s", job.id)
            self._finish(job, context, messenger, state="failed", template="fetch_failed", channel=channel)
            return
        if job.status_message_id is not None:
            messenger.update_job_status(
                job.status_message_id, state="done", text=reply.render("fetch_done", context.lang), terminal=True
            )
        self._ticket.run_bytes(
            user_id=job.user_id,
            lang=context.lang,
            messenger=messenger,
            trace_id=trace_id,
            channel=channel,
            data=data,
            content_type="application/pdf",
            chat_hint=job.project_hint,
            source="web",
            reply_to_id=context.reply_to_id,
        )

    def _handle_not_ready(
        self,
        job: AssistantJobRecord,
        context: _StatusContext,
        messenger: AssistantMessenger,
        trace_id: str,
        channel: Optional[ChannelRef] = None,
    ) -> None:
        attempts = job.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            # Terminal (no further retry, no further enqueue): guarded like every other
            # one-shot outcome (review finding NEW-H2) so the reaper re-running this job
            # after 30 idle minutes can never post "fetch failed" to the user twice.
            if not self._job_repo.mark_processed(job.id):
                logger.info("assistant.fetch_invoice: job %s already processed, skipping", job.id)
                return
            self._job_repo.update_status(job.id, status="failed")
            self._finish(job, context, messenger, state="failed", template="fetch_failed", channel=channel)
            return
        run_after = datetime.now(timezone.utc) + NOT_READY_BACKOFF
        self._job_repo.update_status(job.id, status="queued", attempts=attempts, run_after=run_after)
        if job.status_message_id is not None:
            messenger.update_job_status(
                job.status_message_id,
                state="queued",
                text=reply.render("fetch_not_ready", context.lang),
                terminal=False,
            )

    def _handle_not_found(
        self,
        job: AssistantJobRecord,
        context: _StatusContext,
        messenger: AssistantMessenger,
        trace_id: str,
        channel: Optional[ChannelRef] = None,
    ) -> None:
        # Terminal and one-shot (status stays "not_found" forever afterwards, unlike
        # not_ready) — guarded for the same reap-safety reason as _handle_terminal_once,
        # otherwise the "closest purchases" choice card would get reposted every time the
        # reaper re-enqueues this job.
        if not self._job_repo.mark_processed(job.id):
            logger.info("assistant.fetch_invoice: job %s already processed, skipping", job.id)
            return
        if job.status_message_id is not None:
            messenger.update_job_status(
                job.status_message_id,
                state="not_found",
                text=reply.render("fetch_not_found_prompt", context.lang),
                terminal=True,
            )
        company_ids = [access.company_id for access in self._company_access.list_for_user(job.user_id)]
        projects = writable_projects(self._project_repo, self._authz_reader, job.user_id, company_ids)
        # merchant/amount_ttc/date are nullable at the `assistant_jobs` table level only
        # to accommodate `find_product` jobs (feature A) — every job this feature ever
        # handles is a `fetch_invoice` one, which always sets all three.
        assert job.merchant is not None and job.amount_ttc is not None and job.date is not None
        candidates = self._closest_purchases(projects, job.merchant, float(job.amount_ttc), job.date)
        if not candidates:
            messenger.post_text(
                job.user_id,
                reply.render("fetch_not_found_prompt", context.lang),
                reply_to_id=context.reply_to_id,
                trace_id=trace_id,
                channel=channel,
            )
            return
        options = [
            {
                "label": f"{project.name} – {invoice.issue_date.isoformat()} – {float(invoice.total_amount)}€",
                "action": "fetch_pick_existing",
                "payload": {"invoice_id": str(invoice.id), "job_id": str(job.id)},
            }
            for project, invoice in candidates
        ]
        options.append(
            {"label": reply.render("fetch_none_option", context.lang), "action": "fetch_none", "payload": {}}
        )
        messenger.post_choice(
            job.user_id,
            reply.render("fetch_not_found_prompt", context.lang),
            options,
            reply_to_id=context.reply_to_id,
            trace_id=trace_id,
            channel=channel,
        )

    def _closest_purchases(
        self, projects: list[WritableProject], merchant: str, amount: float, target_date: date
    ) -> list[tuple[WritableProject, InvoiceEntity]]:
        date_from = target_date - CLOSEST_PURCHASE_WINDOW
        date_to = min(target_date + CLOSEST_PURCHASE_WINDOW, date.today() + timedelta(days=1))
        found: list[tuple[WritableProject, InvoiceEntity]] = []
        for project in projects:
            rows = self._invoice_repo.find_by_project_in_range(
                project.id, date_from, date_to, type_filter=InvoiceType.MATERIALS_SERVICES
            )
            for row in rows:
                if _recipient_matches_merchant(row.recipient_name, merchant):
                    found.append((project, row))
        found.sort(
            key=lambda pair: (abs(float(pair[1].total_amount) - amount), abs((pair[1].issue_date - target_date).days))
        )
        return found[:NOT_FOUND_CANDIDATES]

    def _finish(
        self,
        job: AssistantJobRecord,
        context: _StatusContext,
        messenger: AssistantMessenger,
        *,
        state: str,
        template: str,
        channel: Optional[ChannelRef] = None,
    ) -> None:
        text = reply.render(template, context.lang)
        if job.status_message_id is not None:
            messenger.update_job_status(job.status_message_id, state=state, text=text, terminal=True)
        else:  # pragma: no cover - every job created by fetch_invoice() has one
            messenger.post_text(job.user_id, text, reply_to_id=context.reply_to_id, channel=channel)

    # ------------------------------------------------------------------
    # Action taps: not_found's choice
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
        channel: Optional[ChannelRef] = None,
    ) -> bool:
        if action == "fetch_pick_existing":
            invoice_id = payload.get("invoice_id")
            if invoice_id:
                invoice = self._invoice_repo.find_by_id(UUID(str(invoice_id)))
                # Only ever show a card for an invoice on a project the caller can see —
                # closes the "any invoice's details disclosed" IDOR even if a forged
                # `invoice_id` ever reached this far (defense in depth: the
                # SubmitAssistantActionUseCase stored-option check already stops one).
                company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
                projects_by_id = {
                    p.id: p for p in writable_projects(self._project_repo, self._authz_reader, user_id, company_ids)
                }
                if invoice is not None and invoice.project_id in projects_by_id:
                    messenger.post_card(
                        user_id,
                        card_type="invoice",
                        entity_id=invoice.id,
                        project_id=invoice.project_id,
                        title=invoice.recipient_name,
                        subtitle=f"{invoice.issue_date.isoformat()} · {float(invoice.total_amount)}€",
                        badge="confirmed",
                        extra={"invoice_number": invoice.invoice_number, "total_ttc": float(invoice.total_amount)},
                        reply_to_id=message_id,
                        trace_id=trace_id,
                        channel=channel,
                    )
            return True
        if action == "fetch_none":
            messenger.post_text(
                user_id,
                reply.render("fetch_none_of_these", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return True
        return False


__all__ = ["InvoiceFetchFeature", "AMOUNT_DATE_SYSTEM_FR"]
