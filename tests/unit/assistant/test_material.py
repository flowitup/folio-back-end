"""Unit tests for `app.application.assistant.features.material.MaterialFeature` (feature A).

Uses REAL `SqlAlchemyBibliothequeSupplierRepository`/`SqlAlchemyBibliothequeProductRepository`,
`SqlAlchemyAssistantJobRepository` and REAL `CreateProductUseCase`/
`FetchProductImageFromUrlUseCase`/`UploadProductImageUseCase` against an in-memory SQLite
DB (the `session` fixture) so "the product actually landed in the library with the right
supplier/reference" is proven against real persistence. Company membership/permission and
DeepSeek/Jev are simple in-memory fakes.

Owner decision D16 dropped the external web-search/reverse-image providers: feature A
now finds the product with a
`find_product` job on the same `assistant_jobs` table feature B uses, and `on_result`
picks among the browser worker's `ProductCandidate` list — these tests drive that job
lifecycle directly (create via `run()`, complete via `job_repo.update_result()` +
`on_result()`), the same pattern `tests/unit/assistant/test_invoice_fetch.py` uses for
feature B.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from app.application.assistant.features.material import MaterialFeature
from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, MaterialIdent, ProductCandidate, ProductSearchResult
from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
from app.application.bibliotheque.fetch_product_image_from_url_usecase import FetchProductImageFromUrlUseCase
from app.application.bibliotheque.upload_product_image_usecase import UploadProductImageUseCase
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
from app.infrastructure.database.repositories.sqlalchemy_assistant_import_repository import (
    SqlAlchemyAssistantImportRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_assistant_job_repository import (
    SqlAlchemyAssistantJobRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_product_repository import (
    SqlAlchemyBibliothequeProductRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_supplier_repository import (
    SqlAlchemyBibliothequeSupplierRepository,
)
from tests.fakes.ai import ScriptedDecision, ScriptedVision


class FakeMembership:
    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        return True


class FakePermissionChecker:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        return self._allowed

    def has_permission_in_company(self, user_id: UUID, permission_name: str, company_id: UUID) -> bool:
        return self._allowed


@dataclass
class _FakeCompany:
    id: UUID
    legal_name: str


class FakeCompanyRepo:
    def __init__(self, companies: dict[UUID, str]) -> None:
        self._companies = companies

    def find_by_id(self, company_id: UUID) -> Optional[_FakeCompany]:
        name = self._companies.get(company_id)
        return _FakeCompany(id=company_id, legal_name=name) if name else None


@dataclass
class _Access:
    company_id: UUID


class FakeCompanyAccessRepo:
    def __init__(self, company_ids: list[UUID]) -> None:
        self._company_ids = company_ids

    def list_for_user(self, user_id: UUID) -> list[_Access]:
        return [_Access(company_id=cid) for cid in self._company_ids]


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


class FakeProjectRepo:
    """No projects — these tests never exercise the `project_hint` resolution path."""

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Any]:
        return []


class FakeAuthzReader:
    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return None

    def is_platform_ops(self, user_id: UUID) -> bool:
        return False


class RecordingFetchImage:
    """Spy standing in for `FetchProductImageFromUrlUseCase` — proves `_attach_image`
    only ever calls it for a merchant-hosted `image_url` (never for the user's own
    photo fallback, and never when `is_merchant_url` rejected the candidate's URL)."""

    def __init__(self, should_raise: bool = False) -> None:
        self.calls: list[tuple[UUID, str]] = []
        self._should_raise = should_raise

    def execute(self, *, requester_id: UUID, product_id: UUID, url: str) -> None:
        self.calls.append((product_id, url))
        if self._should_raise:
            raise RuntimeError("SSRF-allowlist rejected the URL")


_PHOTO_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-a-material-photo"


class World:
    def __init__(
        self,
        session,
        permission_checker: Optional[FakePermissionChecker] = None,
        fetch_image_usecase: Optional[Any] = None,
    ) -> None:
        self.session = session
        self.user_id = uuid4()
        self.company_id = uuid4()
        self.company_access = FakeCompanyAccessRepo([self.company_id])
        self.company_repo = FakeCompanyRepo({self.company_id: "Ma Société"})
        self.supplier_repo = SqlAlchemyBibliothequeSupplierRepository(session)
        self.product_repo = SqlAlchemyBibliothequeProductRepository(session)
        self.material_imports = SqlAlchemyAssistantImportRepository(session)
        self.job_repo = SqlAlchemyAssistantJobRepository(session)
        self.image_storage = InMemoryDocumentStorage()
        self.membership = FakeMembership()
        self.permission_checker = permission_checker or FakePermissionChecker(allowed=True)
        self.create_product_usecase = CreateProductUseCase(
            self.supplier_repo, self.product_repo, self.membership, self.permission_checker, session
        )
        self.fetch_image_usecase: Any = fetch_image_usecase or FetchProductImageFromUrlUseCase(
            self.product_repo, self.image_storage, self.membership, self.permission_checker, session
        )
        self.upload_image_usecase = UploadProductImageUseCase(
            self.product_repo, self.image_storage, self.membership, self.permission_checker, session
        )
        self.messages = FakeMessageRepo()
        self.messenger = AssistantMessenger(self.messages, FakeSession())
        self.storage = InMemoryDocumentStorage()
        self.vision = ScriptedVision(json_answers=[])
        self.decisions = ScriptedDecision()
        self.feature = MaterialFeature(
            vision=self.vision,
            decisions=self.decisions,
            job_repo=self.job_repo,
            messages=self.messages,
            storage=self.storage,
            company_access=self.company_access,
            company_repo=self.company_repo,
            project_repo=FakeProjectRepo(),
            authz_reader=FakeAuthzReader(),
            product_repo=self.product_repo,
            supplier_repo=self.supplier_repo,
            material_imports=self.material_imports,
            create_product_usecase=self.create_product_usecase,
            fetch_image_usecase=self.fetch_image_usecase,
            upload_image_usecase=self.upload_image_usecase,
        )

    def default_scope(self) -> ChannelScope:
        return ChannelScope(
            kind="company", company_id=self.company_id, project_id=None, is_admin_channel=False, asker_id=self.user_id
        )

    def post_photo(self, photo_bytes: bytes = _PHOTO_BYTES) -> UUID:
        key = f"chat/{uuid4()}"
        self.storage.put(key, io.BytesIO(photo_bytes), content_type="image/jpeg")
        message = ChatMessage.create(
            channel=ChannelRef(kind="assistant", id=self.user_id),
            sender_id=self.user_id,
            body=None,
            attachment=ChatAttachment(
                storage_key=key, filename="material.jpg", content_type="image/jpeg", size_bytes=len(photo_bytes)
            ),
        )
        self.messages.add(message)
        return message.id

    def last_replies(self) -> list[ChatMessage]:
        return sorted(self.messages.messages.values(), key=lambda m: m.created_at)

    def start_search(self, message_id: UUID) -> AssistantJobRecord:
        """Runs `feature.run()` (already scripted with an ident answer) through to job
        creation, and returns the freshly created `find_product` job."""
        outcome = self.feature.run(
            user_id=self.user_id,
            message_id=message_id,
            lang="fr",
            messenger=self.messenger,
            trace_id="t1",
            scope=self.default_scope(),
        )
        assert outcome == "queued"
        jobs = self.job_repo.list_recent_for_user(self.user_id, limit=1)
        assert jobs, "expected a find_product job to have been created"
        return jobs[0]

    def complete_search(
        self, job: AssistantJobRecord, *, status: str = "done", candidates: Optional[list[ProductCandidate]] = None
    ) -> None:
        """Simulates the browser worker writing back a result, then runs `on_result()`
        (the `process_product_search` RQ job's real behaviour)."""
        result = ProductSearchResult(status=status, candidates=candidates or [])
        self.job_repo.update_result(job.id, status=status, result=result.model_dump())
        self.feature.on_result(job.id, messenger=self.messenger, trace_id="t2")


@pytest.fixture
def world(session) -> World:
    return World(session)


def _ident(confidence: float = 0.9, reference: Optional[str] = None) -> MaterialIdent:
    return MaterialIdent(
        brand="Bosch",
        name="Perceuse à percussion",
        reference=reference,
        category="outillage",
        specs="18V, 2 batteries",
        search_queries=["perceuse bosch 18v"],
        confidence=confidence,
    )


def _candidate(
    url: str,
    *,
    merchant: str = "leroymerlin",
    title: str = "Perceuse Bosch 18V",
    reference: Optional[str] = "GSB18V",
    image_url: Optional[str] = None,
) -> ProductCandidate:
    return ProductCandidate(
        url=url,
        title=title,
        merchant=merchant,
        reference=reference,
        brand="Bosch",
        price_ttc=199.0,
        image_url=image_url,
    )


def _pick_decision(label: str, confidence: float):
    from app.application.assistant.ports import Decision

    return Decision(choices={"pick": (label, confidence, {})}, nouls={})


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class TestJobCreation:
    def test_run_creates_a_find_product_job_and_posts_the_ack_template(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]

        world.feature.run(
            user_id=world.user_id,
            message_id=message_id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            scope=world.default_scope(),
        )

        jobs = world.job_repo.list_recent_for_user(world.user_id)
        assert len(jobs) == 1
        assert jobs[0].type == "find_product"
        assert jobs[0].params["photo_sha256"] == _sha(_PHOTO_BYTES)
        job_status = next(m for m in world.last_replies() if m.content_type == "job_status")
        assert job_status.payload["state"] == "queued"
        assert "cherche" in job_status.payload["text"].lower()


class TestChannelRoundTrip:
    """Phase 03's answer to phase 01/02's open question 2: the async `find_product` job
    carries the originating channel, and `on_result` posts back into it."""

    def test_job_carries_the_channel_key_and_on_result_posts_back_into_it(self, world: World) -> None:
        channel = ChannelRef(kind="project", id=uuid4())
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]

        world.feature.run(
            user_id=world.user_id,
            message_id=message_id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            scope=ChannelScope(
                kind="project",
                company_id=world.company_id,
                project_id=channel.id,
                is_admin_channel=False,
                asker_id=world.user_id,
            ),
        )

        job = world.job_repo.list_recent_for_user(world.user_id, limit=1)[0]
        assert job.channel_key == channel.key

        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}
        world.complete_search(job, candidates=[_candidate("https://www.leroymerlin.fr/produit")])
        posted = [m for m in world.last_replies() if m.channel == channel]
        assert posted, "on_result should have posted into the job's originating channel"


class TestConfirmed:
    def test_creates_the_product_and_posts_a_confirmed_card(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.complete_search(job, candidates=[_candidate("https://www.leroymerlin.fr/p/1")])

        products, total = world.product_repo.list(world.company_id)
        assert total == 1
        assert products[0].name == "Perceuse Bosch 18V"
        assert products[0].supplier_reference == "GSB18V"
        supplier = world.supplier_repo.find_by_id(products[0].supplier_id)
        assert supplier is not None and supplier.name == "Leroy Merlin"
        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "confirmed"
        card = next(m for m in world.last_replies() if m.content_type == "card")
        assert card.payload["card"]["badge"] == "confirmed"


class TestToConfirm:
    def test_posts_a_to_confirm_card(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.7)}

        world.complete_search(job, candidates=[_candidate("https://www.pointp.fr/p/2", merchant="pointp")])

        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        card = next(m for m in world.last_replies() if m.content_type == "card")
        assert card.payload["card"]["badge"] == "to_confirm"


class TestNoMatchImportsPhotoOnly:
    def test_creates_a_photo_only_product_when_there_are_no_candidates(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)

        world.complete_search(job, status="not_found", candidates=[])

        products, total = world.product_repo.list(world.company_id)
        assert total == 1
        assert products[0].name == "Perceuse à percussion"
        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        assert cached.source_url is None
        # No browser-agent failure — the neutral "photo only" template is used, not
        # `product_search_failed`.
        assert not any(
            "fournisseurs" in (m.body or "") and "pas pu accéder" in (m.body or "") for m in world.last_replies()
        )

    def test_falls_back_to_photo_only_when_jev_rejects_every_candidate(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("none", 0.95)}

        world.complete_search(job, candidates=[_candidate("https://www.leroymerlin.fr/p/1")])

        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        assert cached.source_url is None


class TestBlockedOrFailed:
    @pytest.mark.parametrize("status", ["blocked", "failed"])
    def test_posts_the_failure_template_then_falls_back_to_photo_only(self, world: World, status: str) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)

        world.complete_search(job, status=status, candidates=[])

        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        texts = [m.body or "" for m in world.last_replies()]
        assert any("fournisseurs" in t for t in texts)


class TestCacheHitBySha:
    def test_replies_with_the_existing_product_and_never_starts_a_search(self, world: World) -> None:
        ident = _ident()
        world.vision._json_answers = [ident]
        existing = world.create_product_usecase.execute(
            requester_id=world.user_id,
            company_id=world.company_id,
            name="Perceuse Bosch 18V",
            supplier_name="Leroy Merlin",
            supplier_reference="GSB18V",
        )
        world.material_imports.add_material_import(
            product_id=existing.id,
            company_id=world.company_id,
            status="confirmed",
            confidence=0.95,
            photo_sha256=_sha(_PHOTO_BYTES),
        )

        message_id = world.post_photo()
        world.feature.run(
            user_id=world.user_id,
            message_id=message_id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            scope=world.default_scope(),
        )

        assert world.job_repo.list_recent_for_user(world.user_id) == []
        card = next(m for m in world.last_replies() if m.content_type == "card")
        assert card.payload["card"]["id"] == str(existing.id)
        assert card.payload["card"]["badge"] == "confirmed"


class TestDedupeByPhotoHash:
    def test_a_second_photo_with_the_same_hash_reuses_the_in_flight_job(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        world.start_search(message_id)

        second_message_id = world.post_photo()
        outcome = world.feature.run(
            user_id=world.user_id,
            message_id=second_message_id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t3",
            scope=world.default_scope(),
        )

        assert outcome == "asked"
        assert len(world.job_repo.list_recent_for_user(world.user_id)) == 1


class TestNullChannelKeyDropsTheReply:
    """M1: a job queued before `channel_key` existed (or with an unparsable one, e.g.
    the retired `assistant:` kind) has nowhere safe to post — the reply is dropped and
    the job is still marked processed, rather than resurrecting the dead fallback
    channel."""

    def test_null_channel_key_drops_the_reply_and_marks_processed(self, world: World) -> None:
        job = world.job_repo.add(
            job_type="find_product",
            user_id=world.user_id,
            project_hint=None,
            lang="fr",
            params={
                "ident": _ident().model_dump(),
                "search_queries": ["perceuse bosch 18v"],
                "company_id": str(world.company_id),
                "photo_sha256": _sha(_PHOTO_BYTES),
                "message_id": str(uuid4()),
            },
            channel_key=None,
        )
        world.job_repo.update_result(
            job.id,
            status="done",
            result=ProductSearchResult(
                status="done", candidates=[_candidate("https://www.leroymerlin.fr/p/1")]
            ).model_dump(),
        )

        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t")

        assert world.last_replies() == []
        updated = world.job_repo.find_by_id(job.id)
        assert updated.processed_at is not None


class TestIdempotentOnResult:
    def test_a_second_on_result_call_for_the_same_job_is_a_no_op(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}
        world.complete_search(job, candidates=[_candidate("https://www.leroymerlin.fr/p/1")])

        cards_before = [m for m in world.last_replies() if m.content_type == "card"]
        world.feature.on_result(job.id, messenger=world.messenger, trace_id="t4")
        cards_after = [m for m in world.last_replies() if m.content_type == "card"]

        assert len(cards_before) == 1
        assert len(cards_after) == 1  # no duplicate card / no duplicate product
        products, total = world.product_repo.list(world.company_id)
        assert total == 1


class TestImageFetchOnlyForMerchantDomain:
    def test_a_merchant_hosted_image_url_is_fetched_server_side(self, session) -> None:
        fetch_spy = RecordingFetchImage()
        world = World(session, fetch_image_usecase=fetch_spy)
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.complete_search(
            job,
            candidates=[
                _candidate("https://www.leroymerlin.fr/p/1", image_url="https://www.leroymerlin.fr/images/gsb18v.jpg")
            ],
        )

        assert fetch_spy.calls and fetch_spy.calls[0][1] == "https://www.leroymerlin.fr/images/gsb18v.jpg"

    def test_a_non_merchant_image_url_is_never_fetched_falls_back_to_the_user_photo(self, session) -> None:
        fetch_spy = RecordingFetchImage()
        world = World(session, fetch_image_usecase=fetch_spy)
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.complete_search(
            job,
            candidates=[_candidate("https://www.leroymerlin.fr/p/1", image_url="https://cdn.evil.example/gsb18v.jpg")],
        )

        assert fetch_spy.calls == []
        products, _total = world.product_repo.list(world.company_id)
        assert products[0].image_storage_key is not None  # fell back to the user's own photo upload


class TestNoPermission:
    def test_posts_the_no_permission_template(self, session) -> None:
        world = World(session, permission_checker=FakePermissionChecker(allowed=False))
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]
        job = world.start_search(message_id)
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.complete_search(job, candidates=[_candidate("https://www.leroymerlin.fr/p/3")])

        products, total = world.product_repo.list(world.company_id)
        assert total == 0
        replies = world.last_replies()
        assert replies[-1].content_type == "text"


class TestDefenseInDepthPickCompanyForeignCompany:
    """C1's defense-in-depth layer (pass-2 review, previously untested): the primary
    defense is `SubmitAssistantActionUseCase`'s stored-option equality check (`_post_
    pick_company` only ever offers the caller's own companies), but `handle_action`
    independently re-checks membership before touching anything else in the payload —
    closing the "attach a product to a foreign company" IDOR even if a forged
    `company_id` ever reached this far."""

    def test_pick_company_refuses_a_company_the_user_is_not_a_member_of(self, world: World) -> None:
        foreign_company_id = uuid4()  # never in world.company_access's list

        handled = world.feature.handle_action(
            user_id=world.user_id,
            message_id=uuid4(),
            action="pick_company",
            payload={"company_id": str(foreign_company_id)},
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            scope=world.default_scope(),
        )

        assert handled is True
        replies = world.last_replies()
        assert len(replies) == 1
        assert replies[0].content_type == "text"
        products, total = world.product_repo.list(foreign_company_id)
        assert total == 0


class TestChannelBoundCompanyResolution:
    """NEW-H1: `_resolve_company` must resolve to the channel's own company (Q1), never
    to another company the asker also belongs to — the same cross-tenant enumeration
    fixed for equipment/router (H2) also let this feature start a `find_product` job,
    and later post its result, under a foreign tenant's `company_id`."""

    def test_never_resolves_to_a_foreign_company_the_asker_also_belongs_to(self, world: World) -> None:
        other_company_id = uuid4()
        # The asker belongs to BOTH world.company_id (the channel's own) and a second
        # company — mirrors a real multi-company user (same setup as H2's equipment test).
        world.feature._company_access = FakeCompanyAccessRepo([world.company_id, other_company_id])
        message_id = world.post_photo()
        world.vision._json_answers = [_ident()]

        job = world.start_search(message_id)

        assert job.params["company_id"] == str(world.company_id)
        assert job.params["company_id"] != str(other_company_id)


class TestLowConfidenceIdentification:
    def test_asks_for_a_clearer_photo(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident(confidence=0.2)]

        world.feature.run(
            user_id=world.user_id,
            message_id=message_id,
            lang="fr",
            messenger=world.messenger,
            trace_id="t1",
            scope=world.default_scope(),
        )

        assert world.job_repo.list_recent_for_user(world.user_id) == []
        products, total = world.product_repo.list(world.company_id)
        assert total == 0
