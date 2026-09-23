"""Feature A — material photo -> company product library.

Pipeline (plan section 3/4, updated by owner decision D16 — no external web-search or
reverse-image provider):
  A1 identify the photo (DeepSeek vision -> ``MaterialIdent``); confidence below
     ``gate.IDENTIFY_MIN`` asks for a clearer photo. Then a cache check: the same photo
     (sha256) or the same supplier reference in the caller's company short-circuits
     straight to a card of the existing product — no browser job, no write.
  A2 a ``find_product`` job is created on ``assistant_jobs`` (the same table/queue
     feature B's ``fetch_invoice`` uses) and a ``job_status`` message is posted
     ("Je cherche la fiche produit…"); the browser worker (``app.infrastructure.
     browser_worker``, real Chrome in the ``ai-browser`` container, restricted to the
     merchant domains below) runs the search out of band and reports back through
     ``process_product_search`` -> ``on_result`` below.
  A3 ``on_result``: Jev picks the best candidate (or "none") from the worker's
     structured result.
  A4 ``_create_from_candidate`` builds the ``Product``-shaped data straight from the
     picked candidate's fields (no extra web call — the browser agent already read the
     page): create/reuse the ``LibraryProduct``, image from the candidate's URL when it
     is on an allow-listed merchant domain, falling back to the user's own photo
     otherwise, then record ``assistant_material_imports``.

No web-search API and no Google Lens fallback: the same agent that fetches invoices
finds the product page, so there is no need for a second provider to identify one.
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from app.application.assistant import gate, reply
from app.application.assistant.exceptions import LlmOutputError, LlmUnavailableError
from app.application.assistant.features._photos import read_photo_bytes
from app.application.assistant.import_ports import MaterialImportRecord, MaterialImportRepositoryPort
from app.application.assistant.jobs_repo import AssistantJobRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, MaterialIdent, ProductCandidate, ProductSearchResult
from app.application.assistant.ports import (
    ChoiceQuestion,
    DecisionPort,
    MessagePosterPort,
    NoulQuestion,
    VisionLlmPort,
)
from app.application.assistant.scope import channel_company_ids
from app.application.authz.ports import AuthzReaderPort
from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    ProductAlreadyExistsError,
)
from app.application.bibliotheque.fetch_product_image_from_url_usecase import (
    FetchProductImageFromUrlUseCase,
    _is_host_allowed,
)
from app.application.bibliotheque.ports import ILibraryProductRepository, ISupplierRepository
from app.application.bibliotheque.upload_product_image_usecase import UploadProductImageUseCase
from app.application.chat.ports import ChatAttachmentStoragePort
from app.application.companies.ports import CompanyRepositoryPort, UserCompanyAccessRepositoryPort
from app.application.projects.ports import IProjectRepository
from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.chat_message import ChannelRef
from app.domain.value_objects.library_category import normalize_category
from app.domain.value_objects.supplier_slug import slugify

logger = logging.getLogger(__name__)

# Keep byte-identical across every call — same rule as extract.py's S1 prompt.
IDENTIFY_SYSTEM_FR = (
    "Tu identifies un matériau ou un produit de construction à partir d'une photo. Réponds uniquement avec un "
    "JSON aux champs : brand, name, reference, ean, category, specs, search_queries (liste de requêtes de "
    "recherche web efficaces pour retrouver ce produit chez un fournisseur français), confidence (0-1). Champs "
    "inconnus → null. N'invente rien."
)
_IDENTIFY_USER_TEXT = "Identifie ce matériau."

#: Merchant domains the browser agent's product search is scoped to (plan section 4,
#: feature A). Kept in lockstep with ``app.infrastructure.browser_worker.merchants.
#: MERCHANT_DOMAINS``/``app.application.assistant.models.MERCHANTS`` (the same 7
#: merchants), duplicated here (not imported from infrastructure) so this application
#: module never depends on ``app.infrastructure``.
_SUPPLIER_NAME_BY_MERCHANT: dict[str, str] = {
    "leroymerlin": "Leroy Merlin",
    "pointp": "Point P",
    "castorama": "Castorama",
    "bricodepot": "Brico Dépôt",
    "gedimat": "Gedimat",
    "technomat": "Technomat",
    "manomano": "ManoMano",
}
MATERIAL_SEARCH_DOMAINS: tuple[str, ...] = (
    "leroymerlin.fr",
    "pointp.fr",
    "castorama.fr",
    "bricodepot.fr",
    "gedimat.fr",
    "technomat.fr",
    "manomano.fr",
)

_UPLOADABLE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

#: Dedupe window for a `find_product` job: a second photo with the same sha256 while a
#: search is already running for it reuses the in-flight job instead of starting a
#: second one (mirrors feature B's `DEDUPE_WINDOW` in `features/invoice_fetch.py`).
DEDUPE_WINDOW = timedelta(hours=24)


def is_merchant_url(url: str) -> bool:
    """True when ``url``'s host is one of the allow-listed merchant domains (or a
    subdomain of one) — gates whether a candidate's ``product_url`` may be stored on a
    library product."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in MATERIAL_SEARCH_DOMAINS)


def _fetchable_image_url(url: Optional[str]) -> Optional[str]:
    """The real gate for ``_attach_image``'s server-side fetch: ``FetchProductImageFrom
    UrlUseCase``'s own SSRF host allowlist, not ``is_merchant_url``'s broader
    7-merchant-site list — the two barely overlap, so gating on ``is_merchant_url``
    made the server-side fetch path dead in practice; every candidate fell back to the
    user's own photo. Returns ``url`` unchanged when fetchable, else ``None``."""
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    return url if host and _is_host_allowed(host) else None


def _valid_product_url(url: Optional[str]) -> Optional[str]:
    """``product_url`` is only ever kept when it is https and on an allow-listed
    merchant host — ``candidate.url`` is text the browser agent read off a web page,
    so a ``javascript:``/arbitrary-scheme value must never reach
    ``bibliotheque_products.product_url``."""
    if not url:
        return None
    try:
        scheme = urlparse(url).scheme
    except ValueError:
        return None
    return url if scheme == "https" and is_merchant_url(url) else None


def _truncate(value: Optional[str], max_length: int) -> Optional[str]:
    """Truncate to a column's max length — the identify/candidate fields are free
    text from a vision model or a scraped web page and have no length bound of their
    own, while ``bibliotheque_products.category``/``size`` are ``String(100)`` and
    ``product_url`` is ``String(500)``. An overflow otherwise raises a DataError
    inside ``on_result``, after the job is already marked processed."""
    if value is None:
        return None
    return value[:max_length] if len(value) > max_length else value


#: Column lengths this feature must never overflow when writing a library product —
#: see ``app.infrastructure.database.models.bibliotheque_product``.
_CATEGORY_MAX_LENGTH = 100
_SIZE_MAX_LENGTH = 100
_PRODUCT_URL_MAX_LENGTH = 500


class MaterialFeature:
    """Implements feature A end to end: run() for a fresh photo, handle_action() for taps,
    on_result() for the browser worker's ``find_product`` job outcome."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        decisions: DecisionPort,
        job_repo: AssistantJobRepositoryPort,
        messages: MessagePosterPort,
        storage: ChatAttachmentStoragePort,
        company_access: UserCompanyAccessRepositoryPort,
        company_repo: CompanyRepositoryPort,
        project_repo: IProjectRepository,
        authz_reader: AuthzReaderPort,
        product_repo: ILibraryProductRepository,
        supplier_repo: ISupplierRepository,
        material_imports: MaterialImportRepositoryPort,
        create_product_usecase: CreateProductUseCase,
        fetch_image_usecase: FetchProductImageFromUrlUseCase,
        upload_image_usecase: UploadProductImageUseCase,
    ) -> None:
        self._vision = vision
        self._decisions = decisions
        self._job_repo = job_repo
        self._messages = messages
        self._storage = storage
        self._company_access = company_access
        self._company_repo = company_repo
        self._project_repo = project_repo
        self._authz_reader = authz_reader
        self._product_repo = product_repo
        self._supplier_repo = supplier_repo
        self._material_imports = material_imports
        self._create_product_usecase = create_product_usecase
        self._fetch_image_usecase = fetch_image_usecase
        self._upload_image_usecase = upload_image_usecase

    # ------------------------------------------------------------------
    # Entry point — a fresh photo (A1)
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
        project_hint: Optional[str] = None,
    ) -> str:
        # `project_hint` is kept for `FeatureHandlersPort.identify_material` interface
        # parity but is otherwise unused: `_resolve_company` now always resolves to the
        # channel's own company (Q1), so there is no cross-company ambiguity left for a
        # project-name hint to break.
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
        photo_bytes, _photo_filename, _photo_mime = photo

        try:
            ident = self._vision.chat_json(
                system=IDENTIFY_SYSTEM_FR, user_text=_IDENTIFY_USER_TEXT, images=[photo_bytes], model_cls=MaterialIdent
            )
        except LlmUnavailableError:
            # A provider outage, not a bad photo — telling the user to retake it would
            # bill another DeepSeek call for a photo that was never the problem.
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
                reply.render("photo_unreadable", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        if not gate.identify_ok(ident.confidence):
            messenger.post_text(
                user_id,
                reply.render("photo_unreadable", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"

        sha = hashlib.sha256(photo_bytes).hexdigest()

        # The photo-hash cache is scoped to a company (review finding H4): resolve the
        # company FIRST, then check the cache — never before, or two different
        # companies photographing the same product could leak each other's product
        # card (and the DB's `(company_id, photo_sha256)` unique constraint would raise
        # on the second company's own otherwise-legitimate import).
        company_id = self._resolve_company(user_id, scope)
        if company_id is None:
            # No cross-company fallback: an asker who is not a member of the channel's
            # own company has no permission here, period — the old fallback offered
            # every OTHER company the asker belongs to, none of which this channel is
            # scoped to, and always failed at the end (the tap's `photo_message_id`
            # was never threaded through).
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"

        cached = self._material_imports.find_by_photo_hash(company_id, sha)
        if cached is not None:
            self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached, scope=scope)
            return "replied"

        return self._continue_after_company(
            user_id, message_id, company_id, ident, sha, lang, messenger, trace_id, scope=scope
        )

    # ------------------------------------------------------------------
    # Company resolution (plan item 5)
    # ------------------------------------------------------------------

    def _resolve_company(self, user_id: UUID, scope: ChannelScope) -> Optional[UUID]:
        """The channel's own company — Q1: no cross-company company picking. A company/
        project/admin channel always pins its own company, so there is nothing left to
        disambiguate by matching ``project_hint`` against every company's projects (the
        old behaviour, which could hand a company-A asker's photo to company B just
        because the caption named a B project by the same/similar name). Returns
        ``None`` when the asker is not a member of the channel's own company, or the
        channel itself has no company — the caller replies ``no_permission`` either
        way (there is no cross-company fallback)."""
        company_ids = channel_company_ids(scope, user_id, self._company_access, self._authz_reader)
        return company_ids[0] if company_ids else None

    # ------------------------------------------------------------------
    # A1 cache-by-reference, then A2 — start the browser product search
    # ------------------------------------------------------------------

    def _continue_after_company(
        self,
        user_id: UUID,
        message_id: UUID,
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        if ident.reference:
            cached = self._material_imports.find_by_reference(company_id, ident.reference)
            if cached is not None:
                self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached, scope=scope)
                return "replied"

        since = datetime.now(timezone.utc) - DEDUPE_WINDOW
        duplicate = self._job_repo.find_duplicate(
            user_id=user_id, job_type="find_product", photo_sha256=sha, since=since
        )
        if duplicate is not None:
            messenger.post_text(
                user_id,
                reply.render("product_search_ack", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"

        params = {
            "ident": ident.model_dump(),
            "search_queries": ident.search_queries or [ident.name],
            "company_id": str(company_id),
            "photo_sha256": sha,
            "message_id": str(message_id),
            "lang": lang,
        }
        job = self._job_repo.add(
            job_type="find_product",
            user_id=user_id,
            project_hint=None,
            lang=lang,
            params=params,
            channel_key=scope.channel.key,
        )
        status_message = messenger.post_job_status(
            user_id,
            job_id=str(job.id),
            state="queued",
            text=reply.render("product_search_ack", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        self._job_repo.set_status_message(job.id, status_message.id)
        return "queued"

    # ------------------------------------------------------------------
    # A3 — the browser worker's result (`process_product_search` RQ job)
    # ------------------------------------------------------------------

    def on_result(self, job_id: UUID, *, messenger: AssistantMessenger, trace_id: str) -> None:
        job = self._job_repo.find_by_id(job_id)
        if job is None:
            logger.warning("assistant.material: job %s not found", job_id)
            return
        # Post back into the channel the request actually came from (phase 03's answer
        # to phase 01/02's open question 2). A job queued before the `channel_key` column
        # existed (or one with a key `ChannelRef.parse` no longer accepts, e.g. the
        # retired `assistant:` kind) has nowhere safe left to post: that channel is gone
        # and no client can read it any more, so the reply is dropped rather than
        # resurrecting the dead fallback (M1) — the job is still marked processed so the
        # reaper does not retry it forever.
        channel: Optional[ChannelRef] = None
        if job.channel_key:
            try:
                channel = ChannelRef.parse(job.channel_key)
            except ValueError:
                logger.warning("assistant.material: job %s has an unparsable channel_key", job.id)
        if channel is None:
            logger.warning("assistant.material: job %s has no resolvable channel_key, dropping its reply", job.id)
            self._job_repo.mark_processed(job.id)
            return
        scope = ChannelScope.for_channel(
            channel, project_company_id=self._authz_reader.project_company_id, asker_id=job.user_id
        )
        if not self._job_repo.mark_processed(job.id):
            # Guards against a duplicate reply/import if `process_product_search` is
            # ever invoked twice for the same job — same one-shot pattern as
            # `InvoiceFetchFeature`'s `mark_processed` calls.
            logger.info("assistant.material: job %s already processed, skipping", job.id)
            return

        params = job.params or {}
        lang = job.lang or "fr"
        reply_to_id = job.status_message_id
        try:
            ident = MaterialIdent.model_validate(params["ident"])
            company_id = UUID(str(params["company_id"]))
            sha = str(params["photo_sha256"])
            photo_message_id = UUID(str(params["message_id"]))
        except (KeyError, ValueError):
            logger.error("assistant.material: job %s has malformed params, dropping", job.id)
            if reply_to_id is not None:
                messenger.update_job_status(
                    reply_to_id, state="failed", text=reply.render("error", lang), terminal=True, scope=scope
                )
            return

        photo = read_photo_bytes(self._messages, self._storage, photo_message_id, job.user_id)
        if photo is None:
            if reply_to_id is not None:
                messenger.update_job_status(
                    reply_to_id, state="failed", text=reply.render("error", lang), terminal=True, scope=scope
                )
            return
        photo_bytes, photo_filename, photo_mime = photo

        if job.status in ("done", "not_found"):
            result = (
                ProductSearchResult.model_validate(job.result or {})
                if job.result
                else ProductSearchResult(status="not_found", candidates=[])
            )
            candidates = result.candidates
            pick_index, pick_confidence = self._pick_candidate(ident, candidates) if candidates else (None, 0.0)
            if pick_index is not None and gate.pick_status(pick_confidence) != "reject":
                candidate = candidates[pick_index]
                status = gate.pick_status(pick_confidence)
                # The job_status message is only moved to a terminal "done" state AFTER
                # the write below returns — posting it first told the user "added to
                # the library" even when `_create_from_candidate` then failed (e.g. the
                # `(company_id, photo_sha256)` IntegrityError when two users send the
                # same photo), leaving no product and a false success.
                try:
                    self._create_from_candidate(
                        job.user_id,
                        reply_to_id,
                        company_id,
                        ident,
                        sha,
                        candidate,
                        status,
                        pick_confidence,
                        photo_bytes,
                        photo_filename,
                        photo_mime,
                        lang,
                        messenger,
                        trace_id,
                        scope=scope,
                    )
                except Exception:
                    logger.exception("assistant.material: failed to create from candidate for job %s", job.id)
                    # The write above can fail mid-transaction (e.g. the (company_id,
                    # photo_sha256) unique violation this handler exists for) and leave
                    # the session unusable for any further statement until it is rolled
                    # back — without this, `update_job_status` below raises too and the
                    # job_status message never leaves "running".
                    messenger.rollback()
                    if reply_to_id is not None:
                        messenger.update_job_status(
                            reply_to_id, state="failed", text=reply.render("error", lang), terminal=True, scope=scope
                        )
                    return
                if reply_to_id is not None:
                    text = reply.render("material_found" if status == "confirmed" else "material_to_confirm", lang)
                    messenger.update_job_status(reply_to_id, state="done", text=text, terminal=True, scope=scope)
                return
            logger.info(
                "assistant.material trace=%s: no confident browser match (candidates=%d) for job %s, falling "
                "back to a photo-only import",
                trace_id,
                len(candidates),
                job.id,
            )
            try:
                self._import_photo_only(
                    job.user_id,
                    reply_to_id,
                    company_id,
                    ident,
                    sha,
                    photo_bytes,
                    photo_filename,
                    photo_mime,
                    lang,
                    messenger,
                    trace_id,
                    scope=scope,
                )
            except Exception:
                logger.exception("assistant.material: photo-only import failed for job %s", job.id)
                # Same reasoning as the candidate-write handler above: roll back the
                # possibly poisoned session before issuing another statement on it.
                messenger.rollback()
                if reply_to_id is not None:
                    messenger.update_job_status(
                        reply_to_id, state="failed", text=reply.render("error", lang), terminal=True, scope=scope
                    )
                return
            if reply_to_id is not None:
                messenger.update_job_status(
                    reply_to_id,
                    state="done",
                    text=reply.render("material_photo_only", lang),
                    terminal=True,
                    scope=scope,
                )
            return

        # blocked / failed (or any other worker-reported status) — one extra template
        # before falling back to the same photo-only import.
        if reply_to_id is not None:
            failed_text = reply.render("product_search_failed", lang)
            messenger.update_job_status(reply_to_id, state="failed", text=failed_text, terminal=True, scope=scope)
            messenger.post_text(
                job.user_id,
                failed_text,
                reply_to_id=reply_to_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
        self._import_photo_only(
            job.user_id,
            reply_to_id,
            company_id,
            ident,
            sha,
            photo_bytes,
            photo_filename,
            photo_mime,
            lang,
            messenger,
            trace_id,
            scope=scope,
        )

    # ------------------------------------------------------------------
    # A3 — Jev pick over the browser worker's candidates
    # ------------------------------------------------------------------

    def _pick_candidate(self, ident: MaterialIdent, candidates: list[ProductCandidate]) -> tuple[Optional[int], float]:
        criteria: dict[str, Optional[str]] = {"none": "aucun ne correspond au matériau identifié"}
        for index, candidate in enumerate(candidates):
            criteria[str(index)] = f"{candidate.title} — {candidate.url}"
        state = {
            "material": ident.model_dump(),
            "candidates": [
                {
                    "title": c.title,
                    "brand": c.brand,
                    "reference": c.reference,
                    "ean": c.ean,
                    "price_ttc": c.price_ttc,
                    "unit": c.unit,
                    "url": c.url,
                }
                for c in candidates
            ],
        }
        questions: dict[str, ChoiceQuestion | NoulQuestion] = {
            "pick": ChoiceQuestion(
                instructions="Le résultat qui correspond le mieux au matériau identifié sur la photo, sinon 'none'.",
                criteria=criteria,
            )
        }
        result = self._decisions.decide(state, questions)
        label, confidence, _probabilities = result.choice("pick")
        if label == "none" or label not in criteria:
            return None, confidence
        try:
            return int(label), confidence
        except ValueError:
            return None, confidence

    # ------------------------------------------------------------------
    # A4 — create/reuse the product, image, import record
    # ------------------------------------------------------------------

    def _create_from_candidate(
        self,
        user_id: UUID,
        message_id: Optional[UUID],
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        candidate: ProductCandidate,
        status: str,
        confidence: float,
        photo_bytes: bytes,
        photo_filename: str,
        photo_mime: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        supplier_name = _SUPPLIER_NAME_BY_MERCHANT.get(candidate.merchant) or ident.brand or "Fournisseur non identifié"
        reference = candidate.reference or candidate.ean or f"AI-{sha[:8]}"
        product = self._get_or_create_product(
            user_id=user_id,
            message_id=message_id,
            company_id=company_id,
            name=candidate.title or ident.name,
            supplier_name=supplier_name,
            reference=reference,
            category=_truncate(normalize_category(ident.category), _CATEGORY_MAX_LENGTH),
            description=ident.specs,
            size=_truncate(candidate.unit, _SIZE_MAX_LENGTH),
            product_url=_truncate(_valid_product_url(candidate.url), _PRODUCT_URL_MAX_LENGTH),
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
        )
        if product is None:
            return "refused"
        if product.image_storage_key is None:
            # Never overwrite a curated image — a manually created product has no
            # import row, so the cache check at `run()`'s photo-hash lookup does not
            # catch this: `_get_or_create_product` can hand back an existing product
            # via `ProductAlreadyExistsError` on ANY subsequent photo of the same
            # reference, including one photographed at a worksite long after an
            # office admin uploaded a proper picture.
            image_url = _fetchable_image_url(candidate.image_url)
            self._attach_image(user_id, product.id, image_url, photo_bytes, photo_mime, photo_filename)
        self._material_imports.add_material_import(
            product_id=product.id,
            company_id=company_id,
            status=status,
            confidence=confidence,
            photo_sha256=sha,
            source_url=candidate.url,
        )
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, status, supplier_name, scope=scope)
        return "created"

    def _import_photo_only(
        self,
        user_id: UUID,
        message_id: Optional[UUID],
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        photo_bytes: bytes,
        photo_filename: str,
        photo_mime: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        supplier_name = ident.brand or "Fournisseur non identifié"
        reference = ident.reference or ident.ean or f"AI-{sha[:8]}"
        product = self._get_or_create_product(
            user_id=user_id,
            message_id=message_id,
            company_id=company_id,
            name=ident.name,
            supplier_name=supplier_name,
            reference=reference,
            category=_truncate(normalize_category(ident.category), _CATEGORY_MAX_LENGTH),
            description=ident.specs,
            size=None,
            product_url=None,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
            scope=scope,
        )
        if product is None:
            return "refused"
        if product.image_storage_key is None:
            # Never overwrite a curated image — see the matching guard in
            # `_create_from_candidate`.
            self._attach_image(user_id, product.id, None, photo_bytes, photo_mime, photo_filename)
        self._material_imports.add_material_import(
            product_id=product.id,
            company_id=company_id,
            status="to_confirm",
            confidence=ident.confidence,
            photo_sha256=sha,
            source_url=None,
        )
        messenger.post_text(
            user_id,
            reply.render("material_photo_only", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        self._reply_product(
            user_id, message_id, lang, messenger, trace_id, product, "to_confirm", supplier_name, scope=scope
        )
        return "created"

    def _get_or_create_product(
        self,
        *,
        user_id: UUID,
        message_id: Optional[UUID],
        company_id: UUID,
        name: str,
        supplier_name: str,
        reference: str,
        category: Optional[str],
        description: Optional[str],
        size: Optional[str],
        product_url: Optional[str],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> Optional[LibraryProduct]:
        try:
            return self._create_product_usecase.execute(
                requester_id=user_id,
                company_id=company_id,
                name=name,
                supplier_name=supplier_name,
                supplier_reference=reference,
                category=category,
                description=description,
                size=size,
                product_url=product_url,
            )
        except ProductAlreadyExistsError:
            existing = self._find_existing_by_reference(company_id, supplier_name, reference)
            if existing is None:
                messenger.post_text(
                    user_id,
                    reply.render("error", lang),
                    reply_to_id=message_id,
                    trace_id=trace_id,
                    channel=scope.channel,
                    scope=scope,
                )
                return None
            return existing
        except (CompanyAccessDeniedError, InsufficientPermissionError):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return None

    def _find_existing_by_reference(
        self, company_id: UUID, supplier_name: str, reference: str
    ) -> Optional[LibraryProduct]:
        supplier = self._supplier_repo.find_by_slug(company_id, slugify(supplier_name))
        if supplier is None:
            return None
        return self._product_repo.find_by_reference(company_id, supplier.id, reference)

    def _attach_image(
        self,
        user_id: UUID,
        product_id: UUID,
        image_url: Optional[str],
        photo_bytes: bytes,
        photo_mime: str,
        photo_filename: str,
    ) -> None:
        if image_url:
            try:
                self._fetch_image_usecase.execute(requester_id=user_id, product_id=product_id, url=image_url)
                return
            except Exception:
                logger.info(
                    "assistant.material: image fetch from %s failed or is not SSRF-allowlisted, falling back to "
                    "the user's own photo",
                    image_url,
                )
        if photo_mime in _UPLOADABLE_IMAGE_TYPES:
            try:
                self._upload_image_usecase.execute(
                    requester_id=user_id,
                    product_id=product_id,
                    fileobj=io.BytesIO(photo_bytes),
                    content_type=photo_mime,
                    filename=photo_filename,
                    size_bytes=len(photo_bytes),
                )
            except Exception:
                logger.warning("assistant.material: failed to upload the fallback photo for product %s", product_id)

    # ------------------------------------------------------------------
    # Replies
    # ------------------------------------------------------------------

    def _reply_existing(
        self,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        cached: MaterialImportRecord,
        scope: ChannelScope,
    ) -> None:
        product = self._product_repo.find_by_id(cached.product_id)
        if product is None:
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        supplier = self._supplier_repo.find_by_id(product.supplier_id)
        supplier_name = supplier.name if supplier is not None else ""
        self._reply_product(
            user_id, message_id, lang, messenger, trace_id, product, cached.status, supplier_name, scope=scope
        )

    def _reply_product(
        self,
        user_id: UUID,
        message_id: Optional[UUID],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        product: LibraryProduct,
        status: str,
        supplier_name: str,
        scope: ChannelScope,
    ) -> None:
        if status == "confirmed":
            messenger.post_text(
                user_id,
                reply.render("material_found", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
        elif status == "to_confirm":
            messenger.post_text(
                user_id,
                reply.render("material_to_confirm", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
        subtitle = f"{supplier_name} · {product.supplier_reference}" if supplier_name else product.supplier_reference
        thumbnail_url = (
            f"/api/v1/bibliotheque/products/{product.id}/image" if product.image_storage_key is not None else None
        )
        messenger.post_card(
            user_id,
            card_type="material",
            entity_id=product.id,
            title=product.name,
            subtitle=subtitle,
            badge=status,
            thumbnail_url=thumbnail_url,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )

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
        """Returns True when this feature handled ``action``, False otherwise.

        Feature A has no action of its own to handle any more: `pick_company` (the
        cross-company fallback) was removed — `run()` now replies `no_permission`
        directly instead of ever posting a choice this method would need to process.
        Kept as a structural implementation of `FeatureHandlersPort`.
        """
        return False


__all__ = ["MaterialFeature", "is_merchant_url", "MATERIAL_SEARCH_DOMAINS", "DEDUPE_WINDOW"]
