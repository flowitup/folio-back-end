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
from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.features._photos import read_photo_bytes
from app.application.assistant.import_ports import MaterialImportRecord, MaterialImportRepositoryPort
from app.application.assistant.jobs_repo import AssistantJobRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import MaterialIdent, ProductCandidate, ProductSearchResult
from app.application.assistant.ports import (
    ChoiceQuestion,
    DecisionPort,
    MessagePosterPort,
    NoulQuestion,
    VisionLlmPort,
)
from app.application.authz.ports import AuthzReaderPort
from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    ProductAlreadyExistsError,
)
from app.application.bibliotheque.fetch_product_image_from_url_usecase import FetchProductImageFromUrlUseCase
from app.application.bibliotheque.ports import ILibraryProductRepository, ISupplierRepository
from app.application.bibliotheque.upload_product_image_usecase import UploadProductImageUseCase
from app.application.chat.ports import ChatAttachmentStoragePort
from app.application.companies.ports import CompanyRepositoryPort, UserCompanyAccessRepositoryPort
from app.application.projects.ports import IProjectRepository
from app.domain.entities.library_product import LibraryProduct
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
    subdomain of one) — gates whether a candidate's ``image_url`` may be fetched
    server-side (``FetchProductImageFromUrlUseCase``) instead of falling back to the
    user's own photo."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in MATERIAL_SEARCH_DOMAINS)


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
        project_hint: Optional[str] = None,
    ) -> str:
        photo = read_photo_bytes(self._messages, self._storage, message_id, user_id)
        if photo is None:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return "asked"
        photo_bytes, _photo_filename, _photo_mime = photo

        try:
            ident = self._vision.chat_json(
                system=IDENTIFY_SYSTEM_FR, user_text=_IDENTIFY_USER_TEXT, images=[photo_bytes], model_cls=MaterialIdent
            )
        except LlmOutputError:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return "asked"
        if not gate.identify_ok(ident.confidence):
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return "asked"

        sha = hashlib.sha256(photo_bytes).hexdigest()

        # The photo-hash cache is scoped to a company (review finding H4): resolve the
        # company FIRST, then check the cache — never before, or two different
        # companies photographing the same product could leak each other's product
        # card (and the DB's `(company_id, photo_sha256)` unique constraint would raise
        # on the second company's own otherwise-legitimate import).
        company_id = self._resolve_company(user_id, project_hint)
        if company_id is None:
            return self._post_pick_company(user_id, message_id, lang, messenger, trace_id, ident, sha, message_id)

        cached = self._material_imports.find_by_photo_hash(company_id, sha)
        if cached is not None:
            self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached)
            return "replied"

        return self._continue_after_company(user_id, message_id, company_id, ident, sha, lang, messenger, trace_id)

    # ------------------------------------------------------------------
    # Company resolution (plan item 5)
    # ------------------------------------------------------------------

    def _resolve_company(self, user_id: UUID, project_hint: Optional[str] = None) -> Optional[UUID]:
        company_ids = [access.company_id for access in self._company_access.list_for_user(user_id)]
        needle = (project_hint or "").strip().lower()
        if needle:
            visible = self._project_repo.list_for_user_and_companies(user_id, company_ids)
            matches = [p for p in visible if p.name.strip().lower() == needle]
            if not matches:
                matches = [p for p in visible if needle in p.name.strip().lower()]
            if len(matches) == 1:
                hinted_company_id = self._authz_reader.project_company_id(matches[0].id)
                if hinted_company_id is not None:
                    return hinted_company_id
        unique_company_ids = set(company_ids)
        if len(unique_company_ids) == 1:
            return next(iter(unique_company_ids))
        return None

    def _post_pick_company(
        self,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        ident: MaterialIdent,
        sha: str,
        photo_message_id: UUID,
    ) -> str:
        options = []
        for access in self._company_access.list_for_user(user_id):
            company = self._company_repo.find_by_id(access.company_id)
            if company is None:
                continue
            options.append(
                {
                    "label": company.legal_name,
                    "action": "pick_company",
                    "payload": {
                        "company_id": str(access.company_id),
                        "message_id": str(photo_message_id),
                        "ident": ident.model_dump(),
                        "sha256": sha,
                    },
                }
            )
        if not options:
            messenger.post_text(user_id, reply.render("no_permission", lang), reply_to_id=message_id, trace_id=trace_id)
            return "refused"
        messenger.post_choice(
            user_id, reply.render("pick_company_prompt", lang), options, reply_to_id=message_id, trace_id=trace_id
        )
        return "asked"

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
    ) -> str:
        if ident.reference:
            cached = self._material_imports.find_by_reference(company_id, ident.reference)
            if cached is not None:
                self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached)
                return "replied"

        since = datetime.now(timezone.utc) - DEDUPE_WINDOW
        duplicate = self._job_repo.find_duplicate(
            user_id=user_id, job_type="find_product", photo_sha256=sha, since=since
        )
        if duplicate is not None:
            messenger.post_text(
                user_id, reply.render("product_search_ack", lang), reply_to_id=message_id, trace_id=trace_id
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
        job = self._job_repo.add(job_type="find_product", user_id=user_id, project_hint=None, lang=lang, params=params)
        status_message = messenger.post_job_status(
            user_id,
            job_id=str(job.id),
            state="queued",
            text=reply.render("product_search_ack", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
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
                    reply_to_id, state="failed", text=reply.render("error", lang), terminal=True
                )
            return

        photo = read_photo_bytes(self._messages, self._storage, photo_message_id, job.user_id)
        if photo is None:
            if reply_to_id is not None:
                messenger.update_job_status(
                    reply_to_id, state="failed", text=reply.render("error", lang), terminal=True
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
                if reply_to_id is not None:
                    text = reply.render("material_found" if status == "confirmed" else "material_to_confirm", lang)
                    messenger.update_job_status(reply_to_id, state="done", text=text, terminal=True)
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
                )
                return
            logger.info(
                "assistant.material trace=%s: no confident browser match (candidates=%d) for job %s, falling "
                "back to a photo-only import",
                trace_id,
                len(candidates),
                job.id,
            )
            if reply_to_id is not None:
                messenger.update_job_status(
                    reply_to_id, state="done", text=reply.render("material_photo_only", lang), terminal=True
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
            )
            return

        # blocked / failed (or any other worker-reported status) — one extra template
        # before falling back to the same photo-only import.
        if reply_to_id is not None:
            failed_text = reply.render("product_search_failed", lang)
            messenger.update_job_status(reply_to_id, state="failed", text=failed_text, terminal=True)
            messenger.post_text(job.user_id, failed_text, reply_to_id=reply_to_id, trace_id=trace_id)
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
            category=ident.category,
            description=ident.specs,
            size=candidate.unit,
            product_url=candidate.url,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        )
        if product is None:
            return "refused"
        image_url = candidate.image_url if candidate.image_url and is_merchant_url(candidate.image_url) else None
        self._attach_image(user_id, product.id, image_url, photo_bytes, photo_mime, photo_filename)
        self._material_imports.add_material_import(
            product_id=product.id,
            company_id=company_id,
            status=status,
            confidence=confidence,
            photo_sha256=sha,
            source_url=candidate.url,
        )
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, status, supplier_name)
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
            category=ident.category,
            description=ident.specs,
            size=None,
            product_url=None,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        )
        if product is None:
            return "refused"
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
            user_id, reply.render("material_photo_only", lang), reply_to_id=message_id, trace_id=trace_id
        )
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, "to_confirm", supplier_name)
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
                messenger.post_text(user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id)
                return None
            return existing
        except (CompanyAccessDeniedError, InsufficientPermissionError):
            messenger.post_text(user_id, reply.render("no_permission", lang), reply_to_id=message_id, trace_id=trace_id)
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
    ) -> None:
        product = self._product_repo.find_by_id(cached.product_id)
        if product is None:
            messenger.post_text(user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id)
            return
        supplier = self._supplier_repo.find_by_id(product.supplier_id)
        supplier_name = supplier.name if supplier is not None else ""
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, cached.status, supplier_name)

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
    ) -> None:
        if status == "confirmed":
            messenger.post_text(
                user_id, reply.render("material_found", lang), reply_to_id=message_id, trace_id=trace_id
            )
        elif status == "to_confirm":
            messenger.post_text(
                user_id, reply.render("material_to_confirm", lang), reply_to_id=message_id, trace_id=trace_id
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
    ) -> bool:
        """Returns True when this feature handled ``action``, False otherwise."""
        if action != "pick_company":
            return False
        company_id = UUID(str(payload["company_id"]))
        # Defense in depth: `_post_pick_company` only ever offers the caller's own
        # companies, but this is cheap insurance against a future caller of this method
        # skipping that guarantee.
        if company_id not in {access.company_id for access in self._company_access.list_for_user(user_id)}:
            messenger.post_text(user_id, reply.render("no_permission", lang), reply_to_id=message_id, trace_id=trace_id)
            return True
        ident = MaterialIdent.model_validate(payload["ident"])
        sha = str(payload["sha256"])
        photo_message_id = UUID(str(payload["message_id"]))
        photo = read_photo_bytes(self._messages, self._storage, photo_message_id, user_id)
        if photo is None:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return True
        self._continue_after_company(user_id, message_id, company_id, ident, sha, lang, messenger, trace_id)
        return True


__all__ = ["MaterialFeature", "is_merchant_url", "MATERIAL_SEARCH_DOMAINS", "DEDUPE_WINDOW"]
