"""Unit tests for `app.application.assistant.features.material.MaterialFeature` (feature A).

Uses REAL `SqlAlchemyBibliothequeSupplierRepository`/`SqlAlchemyBibliothequeProductRepository`
and REAL `CreateProductUseCase`/`FetchProductImageFromUrlUseCase`/`UploadProductImageUseCase`
against an in-memory SQLite DB (the `session` fixture) so "the product actually landed in
the library with the right supplier/reference" is proven against real persistence.
Company membership/permission and Tavily/SerpApi are simple in-memory fakes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from app.application.assistant.features.material import MaterialFeature
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import MaterialIdent, Product
from app.application.bibliotheque.create_product_usecase import CreateProductUseCase
from app.application.bibliotheque.fetch_product_image_from_url_usecase import FetchProductImageFromUrlUseCase
from app.application.bibliotheque.upload_product_image_usecase import UploadProductImageUseCase
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
from app.infrastructure.database.repositories.sqlalchemy_assistant_import_repository import (
    SqlAlchemyAssistantImportRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_product_repository import (
    SqlAlchemyBibliothequeProductRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_supplier_repository import (
    SqlAlchemyBibliothequeSupplierRepository,
)
from tests.fakes.ai import RecordingLens, RecordingWebSearch, ScriptedDecision, ScriptedVision


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

    def list_recent_text(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
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


_PHOTO_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-a-material-photo"


class World:
    def __init__(self, session, permission_checker: Optional[FakePermissionChecker] = None) -> None:
        self.session = session
        self.user_id = uuid4()
        self.company_id = uuid4()
        self.company_access = FakeCompanyAccessRepo([self.company_id])
        self.company_repo = FakeCompanyRepo({self.company_id: "Ma Société"})
        self.supplier_repo = SqlAlchemyBibliothequeSupplierRepository(session)
        self.product_repo = SqlAlchemyBibliothequeProductRepository(session)
        self.material_imports = SqlAlchemyAssistantImportRepository(session)
        self.image_storage = InMemoryDocumentStorage()
        self.membership = FakeMembership()
        self.permission_checker = permission_checker or FakePermissionChecker(allowed=True)
        self.create_product_usecase = CreateProductUseCase(
            self.supplier_repo, self.product_repo, self.membership, self.permission_checker, session
        )
        self.fetch_image_usecase = FetchProductImageFromUrlUseCase(
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
        self.web_search = RecordingWebSearch()
        self.lens = RecordingLens()
        self.feature = MaterialFeature(
            vision=self.vision,
            decisions=self.decisions,
            web_search=self.web_search,
            lens=self.lens,
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


def _hit(url: str, title: str = "Perceuse Bosch 18V") -> dict[str, Any]:
    return {"url": url, "title": title, "content": "Perceuse à percussion Bosch 18V, 2 batteries incluses."}


class TestConfirmed:
    def test_creates_the_product_and_posts_a_confirmed_card(self, world: World) -> None:
        message_id = world.post_photo()
        ident = _ident()
        product = Product(name="Perceuse Bosch 18V", reference="GSB18V", source_url="https://www.leroymerlin.fr/p/1")
        world.vision._json_answers = [ident, product]
        world.web_search._search_result = {"results": [_hit("https://www.leroymerlin.fr/p/1")], "images": []}
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

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
        ident = _ident()
        product = Product(name="Perceuse Bosch 18V", reference="GSB18V", source_url="https://www.pointp.fr/p/2")
        world.vision._json_answers = [ident, product]
        world.web_search._search_result = {"results": [_hit("https://www.pointp.fr/p/2")], "images": []}
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.7)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        card = next(m for m in world.last_replies() if m.content_type == "card")
        assert card.payload["card"]["badge"] == "to_confirm"


class TestNoMatchImportsPhotoOnly:
    def test_creates_a_photo_only_product_when_nothing_matches(self, world: World) -> None:
        message_id = world.post_photo()
        ident = _ident()
        world.vision._json_answers = [ident]
        world.web_search._search_result = {"results": [], "images": []}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        products, total = world.product_repo.list(world.company_id)
        assert total == 1
        assert products[0].name == "Perceuse à percussion"
        cached = world.material_imports.find_by_photo_hash(world.company_id, _sha(_PHOTO_BYTES))
        assert cached is not None
        assert cached.status == "to_confirm"
        assert cached.source_url is None
        assert world.lens.calls == []  # chat photos never have a public URL to send Lens


class TestCacheHitBySha:
    def test_replies_with_the_existing_product_and_skips_the_web_search(self, world: World) -> None:
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
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        assert world.web_search.search_calls == []
        card = next(m for m in world.last_replies() if m.content_type == "card")
        assert card.payload["card"]["id"] == str(existing.id)
        assert card.payload["card"]["badge"] == "confirmed"


class TestNoPermission:
    def test_posts_the_no_permission_template(self, session) -> None:
        world = World(session, permission_checker=FakePermissionChecker(allowed=False))
        message_id = world.post_photo()
        ident = _ident()
        product = Product(name="Perceuse Bosch 18V", reference="GSB18V", source_url="https://www.leroymerlin.fr/p/3")
        world.vision._json_answers = [ident, product]
        world.web_search._search_result = {"results": [_hit("https://www.leroymerlin.fr/p/3")], "images": []}
        world.decisions._by_question_keys = {frozenset({"pick"}): _pick_decision("0", 0.9)}

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

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
        )

        assert handled is True
        replies = world.last_replies()
        assert len(replies) == 1
        assert replies[0].content_type == "text"
        products, total = world.product_repo.list(foreign_company_id)
        assert total == 0


class TestLowConfidenceIdentification:
    def test_asks_for_a_clearer_photo(self, world: World) -> None:
        message_id = world.post_photo()
        world.vision._json_answers = [_ident(confidence=0.2)]

        world.feature.run(
            user_id=world.user_id, message_id=message_id, lang="fr", messenger=world.messenger, trace_id="t1"
        )

        assert world.web_search.search_calls == []
        products, total = world.product_repo.list(world.company_id)
        assert total == 0


def _pick_decision(label: str, confidence: float):
    from app.application.assistant.ports import Decision

    return Decision(choices={"pick": (label, confidence, {})}, nouls={})


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
