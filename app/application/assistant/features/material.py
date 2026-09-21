"""Feature A — material photo -> company product library.

Pipeline (plan section 3/4):
  A1 identify the photo (DeepSeek vision -> ``MaterialIdent``); confidence below
     ``gate.IDENTIFY_MIN`` asks for a clearer photo. Then a cache check: the same photo
     (sha256) or the same supplier reference in the caller's company short-circuits
     straight to a card of the existing product — no web call, no write.
  A2 Tavily search across the allow-listed merchant domains, one query at a time until
     >= 3 hits or the queries run out.
  A3 Jev picks the best hit (or 'none') from the search results.
  A4 Tavily ``extract`` on the picked hit's URL + a text-only DeepSeek call -> ``Product``.
  A5 create/reuse the ``LibraryProduct`` (supplier resolved from the hit's domain),
     image from the hit's URL falling back to the user's own photo, then record
     ``assistant_material_imports``.

SerpApi Lens (A3's low-confidence fallback in the plan) needs a PUBLIC image URL; a
chat photo lives in private S3 storage, so this phase always skips it (logged) and goes
straight to a photo-only import — the port is still accepted here so a later phase can
wire a real public-URL path without changing this feature's constructor.
"""

from __future__ import annotations

import hashlib
import io
import logging
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from app.application.assistant import gate, reply
from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.features._photos import read_photo_bytes
from app.application.assistant.import_ports import MaterialImportRecord, MaterialImportRepositoryPort
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import MaterialIdent, Product
from app.application.assistant.ports import (
    ChoiceQuestion,
    DecisionPort,
    LensPort,
    MessagePosterPort,
    NoulQuestion,
    VisionLlmPort,
    WebSearchPort,
)
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

PRODUCT_SYSTEM_FR = (
    "Tu lis la fiche produit d'un fournisseur de matériaux de construction français. Réponds uniquement avec un "
    "JSON aux champs : name, brand, reference, ean, price_ttc, unit, image_url, source_url. source_url doit "
    "être recopié exactement depuis la ligne 'URL:' fournie. Champs absents → null. N'invente rien."
)

#: Merchant domains Tavily is scoped to (plan section 4, feature A / Tavily search).
_SUPPLIER_BY_DOMAIN: dict[str, str] = {
    "leroymerlin.fr": "Leroy Merlin",
    "pointp.fr": "Point P",
    "castorama.fr": "Castorama",
    "bricodepot.fr": "Brico Dépôt",
    "gedimat.fr": "Gedimat",
    "technomat.fr": "Technomat",
    "manomano.fr": "ManoMano",
}
MATERIAL_SEARCH_DOMAINS: tuple[str, ...] = tuple(_SUPPLIER_BY_DOMAIN)

_MIN_HITS = 3
_MAX_HITS = 6
_UPLOADABLE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def _supplier_name_for_url(url: str) -> Optional[str]:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    host = host[4:] if host.startswith("www.") else host
    for domain, name in _SUPPLIER_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


class MaterialFeature:
    """Implements feature A end to end: run() for a fresh photo, handle_action() for taps."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        decisions: DecisionPort,
        web_search: WebSearchPort,
        lens: LensPort,
        messages: MessagePosterPort,
        storage: ChatAttachmentStoragePort,
        company_access: UserCompanyAccessRepositoryPort,
        company_repo: CompanyRepositoryPort,
        product_repo: ILibraryProductRepository,
        supplier_repo: ISupplierRepository,
        material_imports: MaterialImportRepositoryPort,
        create_product_usecase: CreateProductUseCase,
        fetch_image_usecase: FetchProductImageFromUrlUseCase,
        upload_image_usecase: UploadProductImageUseCase,
    ) -> None:
        self._vision = vision
        self._decisions = decisions
        self._web_search = web_search
        self._lens = lens
        self._messages = messages
        self._storage = storage
        self._company_access = company_access
        self._company_repo = company_repo
        self._product_repo = product_repo
        self._supplier_repo = supplier_repo
        self._material_imports = material_imports
        self._create_product_usecase = create_product_usecase
        self._fetch_image_usecase = fetch_image_usecase
        self._upload_image_usecase = upload_image_usecase

    # ------------------------------------------------------------------
    # Entry point — a fresh photo (A1)
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
            ident = self._vision.chat_json(
                system=IDENTIFY_SYSTEM_FR, user_text=_IDENTIFY_USER_TEXT, images=[photo_bytes], model_cls=MaterialIdent
            )
        except LlmOutputError:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return
        if not gate.identify_ok(ident.confidence):
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return

        sha = hashlib.sha256(photo_bytes).hexdigest()
        cached = self._material_imports.find_by_photo_hash(sha)
        if cached is not None:
            self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached)
            return

        company_id = self._resolve_company(user_id)
        if company_id is None:
            self._post_pick_company(user_id, message_id, lang, messenger, trace_id, ident, sha, message_id)
            return

        self._continue_after_company(
            user_id,
            message_id,
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
    # Company resolution (plan item 5)
    # ------------------------------------------------------------------

    def _resolve_company(self, user_id: UUID) -> Optional[UUID]:
        company_ids = {access.company_id for access in self._company_access.list_for_user(user_id)}
        if len(company_ids) == 1:
            return next(iter(company_ids))
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
    ) -> None:
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
            return
        messenger.post_choice(
            user_id, reply.render("pick_company_prompt", lang), options, reply_to_id=message_id, trace_id=trace_id
        )

    # ------------------------------------------------------------------
    # A1 cache-by-reference, A2-A5
    # ------------------------------------------------------------------

    def _continue_after_company(
        self,
        user_id: UUID,
        message_id: UUID,
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        photo_bytes: bytes,
        photo_filename: str,
        photo_mime: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        if ident.reference:
            cached = self._material_imports.find_by_reference(company_id, ident.reference)
            if cached is not None:
                self._reply_existing(user_id, message_id, lang, messenger, trace_id, cached)
                return

        hits = self._search(ident)
        if hits:
            pick_index, pick_confidence = self._pick(ident, hits)
        else:
            pick_index, pick_confidence = None, 0.0

        if pick_index is None or gate.pick_status(pick_confidence) == "reject":
            logger.info(
                "assistant.material trace=%s: no confident web match (hits=%d); Lens needs a public image URL "
                "chat photos do not have, skipping straight to a photo-only import",
                trace_id,
                len(hits),
            )
            self._import_photo_only(
                user_id,
                message_id,
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

        hit = hits[pick_index]
        status = gate.pick_status(pick_confidence)
        product_data = self._extract_product(hit)
        self._create_from_hit(
            user_id,
            message_id,
            company_id,
            ident,
            sha,
            hit,
            product_data,
            status,
            pick_confidence,
            photo_bytes,
            photo_filename,
            photo_mime,
            lang,
            messenger,
            trace_id,
        )

    # ------------------------------------------------------------------
    # A2 — web search
    # ------------------------------------------------------------------

    def _search(self, ident: MaterialIdent) -> list[dict[str, Any]]:
        queries = ident.search_queries or [ident.name]
        hits: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for query in queries:
            if len(hits) >= _MIN_HITS:
                break
            result = self._web_search.search(
                query, include_domains=list(MATERIAL_SEARCH_DOMAINS), include_images=True, max_results=_MAX_HITS
            )
            for item in result.get("results", []):
                url = item.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                hits.append(item)
        return hits[:_MAX_HITS]

    # ------------------------------------------------------------------
    # A3 — Jev pick
    # ------------------------------------------------------------------

    def _pick(self, ident: MaterialIdent, hits: list[dict[str, Any]]) -> tuple[Optional[int], float]:
        criteria: dict[str, Optional[str]] = {"none": "aucun ne correspond au matériau identifié"}
        for index, hit in enumerate(hits):
            criteria[str(index)] = f"{hit.get('title', '')} — {hit.get('url', '')}"
        state = {
            "material": ident.model_dump(),
            "hits": [
                {"title": hit.get("title"), "url": hit.get("url"), "content": (hit.get("content") or "")[:500]}
                for hit in hits
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
    # A4 — extract + text-only DeepSeek call
    # ------------------------------------------------------------------

    def _extract_product(self, hit: dict[str, Any]) -> Product:
        url = str(hit.get("url") or "")
        extracted = self._web_search.extract([url], include_images=True)
        results = extracted.get("results", [])
        raw_content = str(results[0].get("raw_content") or "") if results else str(hit.get("content") or "")
        images = results[0].get("images", []) if results else []
        user_text = f"URL: {url}\n\nContenu:\n{raw_content[:8000]}"
        try:
            product = self._vision.chat_json(
                system=PRODUCT_SYSTEM_FR, user_text=user_text, images=[], model_cls=Product
            )
        except LlmOutputError:
            product = Product(name=str(hit.get("title") or "Produit"), source_url=url)
        product.source_url = url
        if not product.image_url and images:
            product.image_url = str(images[0])
        return product

    # ------------------------------------------------------------------
    # A5 — create/reuse the product, image, import record
    # ------------------------------------------------------------------

    def _create_from_hit(
        self,
        user_id: UUID,
        message_id: UUID,
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        hit: dict[str, Any],
        product_data: Product,
        status: str,
        confidence: float,
        photo_bytes: bytes,
        photo_filename: str,
        photo_mime: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
        supplier_name = _supplier_name_for_url(str(hit.get("url") or "")) or ident.brand or "Fournisseur non identifié"
        reference = product_data.reference or product_data.ean or f"AI-{sha[:8]}"
        product = self._get_or_create_product(
            user_id=user_id,
            message_id=message_id,
            company_id=company_id,
            name=product_data.name or ident.name,
            supplier_name=supplier_name,
            reference=reference,
            category=ident.category,
            description=ident.specs,
            size=product_data.unit,
            product_url=product_data.source_url,
            lang=lang,
            messenger=messenger,
            trace_id=trace_id,
        )
        if product is None:
            return
        self._attach_image(user_id, product.id, product_data.image_url, photo_bytes, photo_mime, photo_filename)
        self._material_imports.add_material_import(
            product_id=product.id,
            status=status,
            confidence=confidence,
            photo_sha256=sha,
            source_url=product_data.source_url,
        )
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, status, supplier_name)

    def _import_photo_only(
        self,
        user_id: UUID,
        message_id: UUID,
        company_id: UUID,
        ident: MaterialIdent,
        sha: str,
        photo_bytes: bytes,
        photo_filename: str,
        photo_mime: str,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> None:
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
            return
        self._attach_image(user_id, product.id, None, photo_bytes, photo_mime, photo_filename)
        self._material_imports.add_material_import(
            product_id=product.id, status="to_confirm", confidence=ident.confidence, photo_sha256=sha, source_url=None
        )
        messenger.post_text(
            user_id, reply.render("material_photo_only", lang), reply_to_id=message_id, trace_id=trace_id
        )
        self._reply_product(user_id, message_id, lang, messenger, trace_id, product, "to_confirm", supplier_name)

    def _get_or_create_product(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
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
        message_id: UUID,
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
        message_id: UUID,
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
        card = {
            "kind": "material",
            "product_id": str(product.id),
            "title": product.name,
            "subtitle": subtitle,
            "badge": status,
            "has_image": product.image_storage_key is not None,
        }
        messenger.post_card(user_id, card, reply_to_id=message_id, trace_id=trace_id)

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
        ident = MaterialIdent.model_validate(payload["ident"])
        sha = str(payload["sha256"])
        photo_message_id = UUID(str(payload["message_id"]))
        photo = read_photo_bytes(self._messages, self._storage, photo_message_id)
        if photo is None:
            messenger.post_text(
                user_id, reply.render("photo_unreadable", lang), reply_to_id=message_id, trace_id=trace_id
            )
            return True
        photo_bytes, photo_filename, photo_mime = photo
        self._continue_after_company(
            user_id,
            message_id,
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
        return True
