"""Tests for code-review fixes applied to the billing module.

Covers:
  C1 — SSRF: _fetch_logo / _validate_logo_url
  H1 — project:read authorization in use-cases
  H2 — clone CHECK constraint (kind-incompatible fields zeroed out)
  H5 — schema bounds (items max_length, free-text max_length, Decimal limits)
  M2 — template duplicate name → 409
  M3 — update kind-incompatible fields (covered in test_update_billing_document.py)
  M5 — convert IntegrityError → 409 (route-level; tested in test_convert_route.py)
  M8 — prefix_override charset validation
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.application.billing.clone_billing_document_usecase import CloneBillingDocumentUseCase
from app.application.billing.create_billing_document_usecase import CreateBillingDocumentUseCase
from app.application.billing.create_template_usecase import CreateTemplateUseCase
from app.application.billing.dtos import (
    CloneBillingDocumentInput,
    CreateBillingDocumentInput,
    CreateTemplateInput,
    ItemInput,
)
from app.api.v1.billing.schemas import (
    CreateBillingDocumentRequest,
    UpsertCompanyProfileRequest,
)
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import (
    BillingTemplateNameConflictError,
    ForbiddenProjectAccessError,
)
from app.infrastructure.pdf.billing_document_pdf_renderer import _validate_logo_url
from tests.unit.application.billing.conftest import (
    InMemoryBillingDocumentRepository,
    InMemoryBillingNumberCounterRepository,
    InMemoryBillingTemplateRepository,
    InMemoryCompanyProfileRepository,
    _FakeSession,
    make_doc,
    make_profile,
)


# ---------------------------------------------------------------------------
# Minimal in-memory project repository for H1 tests
# ---------------------------------------------------------------------------


class _Project:
    """Minimal project stub for authorization checks."""

    def __init__(self, owner_id: UUID, user_ids=None):
        self.id = uuid4()
        self.owner_id = owner_id
        self.user_ids = user_ids or []


class InMemoryProjectRepository:
    def __init__(self):
        self._store: dict[UUID, _Project] = {}

    def find_by_id(self, project_id: UUID) -> Optional[_Project]:
        return self._store.get(project_id)

    def save(self, project: _Project) -> _Project:
        self._store[project.id] = project
        return project


# ---------------------------------------------------------------------------
# C1 — SSRF: _validate_logo_url
# ---------------------------------------------------------------------------


class TestValidateLogoUrl:
    def test_accepts_https_url(self):
        """Valid public HTTPS URL should not raise."""
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="93.184.216.34"
        ):
            _validate_logo_url("https://cdn.example.com/logo.png")

    def test_accepts_http_url(self):
        """Valid public HTTP URL should not raise."""
        with patch("app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="8.8.8.8"):
            _validate_logo_url("http://cdn.example.com/logo.png")

    def test_rejects_file_scheme(self):
        """file:// scheme must be rejected immediately (no network I/O)."""
        with pytest.raises(ValueError, match="scheme"):
            _validate_logo_url("file:///etc/hosts")

    def test_rejects_ftp_scheme(self):
        """ftp:// scheme must be rejected."""
        with pytest.raises(ValueError, match="scheme"):
            _validate_logo_url("ftp://files.example.com/logo.png")

    def test_rejects_loopback_ip(self):
        """http://127.0.0.1 must be blocked (loopback)."""
        # IP is a literal in the URL; gethostbyname resolves it to itself
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="127.0.0.1"
        ):
            with pytest.raises(ValueError):
                _validate_logo_url("http://127.0.0.1/logo.png")

    def test_rejects_private_ip(self):
        """http://10.0.0.1 must be blocked (private RFC-1918)."""
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="10.0.0.1"
        ):
            with pytest.raises(ValueError):
                _validate_logo_url("http://10.0.0.1/logo.png")

    def test_rejects_link_local_aws_metadata(self):
        """http://169.254.169.254 must be blocked (link-local / AWS metadata)."""
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname",
            return_value="169.254.169.254",
        ):
            with pytest.raises(ValueError, match="AWS|non-public"):
                _validate_logo_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_private_class_b(self):
        """http://172.16.0.1 must be blocked (private RFC-1918 Class B)."""
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="172.16.0.1"
        ):
            with pytest.raises(ValueError):
                _validate_logo_url("http://172.16.0.1/logo.png")

    def test_rejects_private_class_c(self):
        """http://192.168.1.1 must be blocked (private RFC-1918 Class C)."""
        with patch(
            "app.infrastructure.pdf.billing_document_pdf_renderer.socket.gethostbyname", return_value="192.168.1.1"
        ):
            with pytest.raises(ValueError):
                _validate_logo_url("http://192.168.1.1/logo.png")


# ---------------------------------------------------------------------------
# C1 — Schema: logo_url must be HttpUrl (http/https only)
# ---------------------------------------------------------------------------


class TestLogoUrlSchemaValidation:
    def test_logo_url_accepts_https(self):
        """HttpUrl field accepts https scheme."""
        body = UpsertCompanyProfileRequest(
            legal_name="Test SAS",
            address="1 rue Test",
            logo_url="https://cdn.example.com/logo.png",
        )
        assert body.logo_url is not None

    def test_logo_url_rejects_file_scheme(self):
        """HttpUrl field rejects file:// at Pydantic validation stage."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest(
                legal_name="Test SAS",
                address="1 rue Test",
                logo_url="file:///etc/hosts",
            )

    def test_logo_url_rejects_ftp_scheme(self):
        """HttpUrl field rejects ftp:// at Pydantic validation stage."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest(
                legal_name="Test SAS",
                address="1 rue Test",
                logo_url="ftp://files.example.com/logo.png",
            )


# ---------------------------------------------------------------------------
# H1 — project:read authorization in CreateBillingDocumentUseCase
# ---------------------------------------------------------------------------


class TestH1ProjectAuthorizationCreate:
    @pytest.fixture
    def user_id(self):
        return uuid4()

    @pytest.fixture
    def other_user_id(self):
        return uuid4()

    @pytest.fixture
    def doc_repo(self):
        return InMemoryBillingDocumentRepository()

    @pytest.fixture
    def counter_repo(self):
        return InMemoryBillingNumberCounterRepository()

    @pytest.fixture
    def profile_repo(self, user_id):
        repo = InMemoryCompanyProfileRepository()
        repo.save(make_profile(user_id))
        return repo

    @pytest.fixture
    def project_repo(self):
        return InMemoryProjectRepository()

    @pytest.fixture
    def usecase(self, doc_repo, counter_repo, profile_repo, project_repo):
        return CreateBillingDocumentUseCase(
            doc_repo=doc_repo,
            counter_repo=counter_repo,
            profile_repo=profile_repo,
            project_repo=project_repo,
        )

    def test_user_cannot_create_doc_for_unowned_project(self, usecase, user_id, other_user_id, project_repo):
        """H1: user not member of project → ForbiddenProjectAccessError."""
        project = _Project(owner_id=other_user_id)
        project_repo.save(project)

        inp = CreateBillingDocumentInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            recipient_name="Client Corp",
            items=[
                ItemInput(description="S", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
            ],
            project_id=project.id,
        )
        with pytest.raises(ForbiddenProjectAccessError):
            usecase.execute(inp, _FakeSession())

    def test_owner_can_create_doc_with_project(self, usecase, user_id, project_repo):
        """H1: project owner can create a doc linked to their project."""
        project = _Project(owner_id=user_id)
        project_repo.save(project)

        inp = CreateBillingDocumentInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            recipient_name="Client Corp",
            items=[
                ItemInput(description="S", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
            ],
            project_id=project.id,
        )
        result = usecase.execute(inp, _FakeSession())
        assert result.project_id == project.id

    def test_member_can_create_doc_with_project(self, usecase, user_id, other_user_id, project_repo):
        """H1: project member can create a doc linked to the project."""
        project = _Project(owner_id=other_user_id, user_ids=[user_id])
        project_repo.save(project)

        inp = CreateBillingDocumentInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            recipient_name="Client Corp",
            items=[
                ItemInput(description="S", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
            ],
            project_id=project.id,
        )
        result = usecase.execute(inp, _FakeSession())
        assert result.project_id == project.id

    def test_no_project_id_skips_auth_check(self, usecase, user_id):
        """H1: no project_id → no auth check → succeeds normally."""
        inp = CreateBillingDocumentInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            recipient_name="Client Corp",
            items=[
                ItemInput(description="S", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
            ],
        )
        result = usecase.execute(inp, _FakeSession())
        assert result.project_id is None


# ---------------------------------------------------------------------------
# H2 — Clone CHECK constraint: kind-incompatible fields zeroed out
# ---------------------------------------------------------------------------


class TestH2CloneKindIncompatibleFields:
    @pytest.fixture
    def user_id(self):
        return uuid4()

    @pytest.fixture
    def doc_repo(self):
        return InMemoryBillingDocumentRepository()

    @pytest.fixture
    def counter_repo(self):
        return InMemoryBillingNumberCounterRepository()

    @pytest.fixture
    def profile_repo(self, user_id):
        repo = InMemoryCompanyProfileRepository()
        repo.save(make_profile(user_id))
        return repo

    @pytest.fixture
    def usecase(self, doc_repo, counter_repo, profile_repo):
        return CloneBillingDocumentUseCase(
            doc_repo=doc_repo,
            counter_repo=counter_repo,
            profile_repo=profile_repo,
        )

    def test_facture_to_devis_clone_removes_payment_fields(self, usecase, doc_repo, user_id):
        """H2: Cloning a facture (with payment_terms) as devis zeros out payment_terms."""
        facture = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            doc_number="FAC-2026-001",
            payment_terms="Net 30",
            payment_due_date=date(2026, 6, 1),
        )
        doc_repo.save(facture)

        inp = CloneBillingDocumentInput(
            source_id=facture.id,
            user_id=user_id,
            override_kind=BillingDocumentKind.DEVIS,
        )
        # Must not raise (InMemory repo has no DB CHECK, but verifies business logic)
        result = usecase.execute(inp, _FakeSession())
        assert result.kind == "devis"
        assert result.payment_terms is None

    def test_devis_to_facture_clone_succeeds(self, usecase, doc_repo, user_id):
        """H2: Cloning a devis as facture succeeds (validity_until cleared by _build_doc_from_inputs)."""
        devis = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            doc_number="DEV-2026-001",
        )
        doc_repo.save(devis)

        inp = CloneBillingDocumentInput(
            source_id=devis.id,
            user_id=user_id,
            override_kind=BillingDocumentKind.FACTURE,
        )
        result = usecase.execute(inp, _FakeSession())
        assert result.kind == "facture"
        # validity_until is a devis-only field; facture result should not have it set
        assert result.validity_until is None

    def test_same_kind_clone_preserves_payment_terms(self, usecase, doc_repo, user_id):
        """H2: Same-kind clone preserves payment_terms (no zeroing)."""
        facture = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            doc_number="FAC-2026-002",
            payment_terms="Net 30",
        )
        doc_repo.save(facture)

        inp = CloneBillingDocumentInput(source_id=facture.id, user_id=user_id)
        result = usecase.execute(inp, _FakeSession())
        assert result.kind == "facture"
        assert result.payment_terms == "Net 30"


# ---------------------------------------------------------------------------
# H5 — Schema bounds
# ---------------------------------------------------------------------------


class TestH5SchemaBounds:
    """Input validation: items max_length, free-text max_length, Decimal limits."""

    def _base_item(self, **overrides) -> dict:
        base = {
            "description": "Service",
            "quantity": "1",
            "unit_price": "100",
            "vat_rate": "20",
        }
        base.update(overrides)
        return base

    def _base_create(self, **overrides) -> dict:
        body = {
            "kind": "devis",
            "recipient_name": "Client",
            "items": [self._base_item()],
        }
        body.update(overrides)
        return body

    def test_items_max_200_accepted(self):
        """200 items is at the boundary — must pass."""
        body = self._base_create(items=[self._base_item() for _ in range(200)])
        req = CreateBillingDocumentRequest.model_validate(body)
        assert len(req.items) == 200

    def test_items_201_rejected(self):
        """201 items exceeds max_length=200 — must raise ValidationError."""
        body = self._base_create(items=[self._base_item() for _ in range(201)])
        with pytest.raises(ValidationError):
            CreateBillingDocumentRequest.model_validate(body)

    def test_description_500_chars_accepted(self):
        """500-char description is at the boundary — must pass."""
        body = self._base_create(items=[self._base_item(description="x" * 500)])
        req = CreateBillingDocumentRequest.model_validate(body)
        assert len(req.items[0].description) == 500

    def test_description_501_chars_rejected(self):
        """501-char description exceeds max_length=500 — must raise."""
        body = self._base_create(items=[self._base_item(description="x" * 501)])
        with pytest.raises(ValidationError):
            CreateBillingDocumentRequest.model_validate(body)

    def test_quantity_max_boundary_accepted(self):
        """quantity=9999999 is at the upper boundary — must pass."""
        body = self._base_create(items=[self._base_item(quantity="9999999")])
        CreateBillingDocumentRequest.model_validate(body)

    def test_quantity_over_limit_rejected(self):
        """quantity=1e10 exceeds le=9999999 — must raise."""
        body = self._base_create(items=[self._base_item(quantity="10000000")])
        with pytest.raises(ValidationError):
            CreateBillingDocumentRequest.model_validate(body)

    def test_notes_max_2000_accepted(self):
        """notes=2000 chars is at the boundary — must pass."""
        body = self._base_create(notes="x" * 2000)
        CreateBillingDocumentRequest.model_validate(body)

    def test_notes_2001_rejected(self):
        """notes=2001 chars exceeds max_length=2000 — must raise."""
        body = self._base_create(notes="x" * 2001)
        with pytest.raises(ValidationError):
            CreateBillingDocumentRequest.model_validate(body)


# ---------------------------------------------------------------------------
# M2 — Template duplicate name → BillingTemplateNameConflictError
# ---------------------------------------------------------------------------


class TestM2TemplateDuplicateName:
    @pytest.fixture
    def user_id(self):
        return uuid4()

    @pytest.fixture
    def template_repo(self):
        return InMemoryBillingTemplateRepository()

    @pytest.fixture
    def usecase(self, template_repo):
        return CreateTemplateUseCase(template_repo=template_repo)

    def test_duplicate_name_raises_conflict_error(self, usecase, template_repo, user_id):
        """M2: repo.save() raising IntegrityError → BillingTemplateNameConflictError."""
        inp = CreateTemplateInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="Dup Template",
        )

        # Simulate IntegrityError from the repo on the second save
        original_save = template_repo.save

        call_count = [0]

        def save_raises_on_second(template):
            call_count[0] += 1
            if call_count[0] > 1:
                raise IntegrityError("UNIQUE constraint failed", {}, None)
            return original_save(template)

        template_repo.save = save_raises_on_second

        # First call succeeds
        usecase.execute(inp, _FakeSession())

        # Second call with same name → IntegrityError → BillingTemplateNameConflictError
        inp2 = CreateTemplateInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="Dup Template",
        )
        with pytest.raises(BillingTemplateNameConflictError):
            usecase.execute(inp2, _FakeSession())


# ---------------------------------------------------------------------------
# M8 — prefix_override charset validation
# ---------------------------------------------------------------------------


class TestM8PrefixOverrideCharset:
    def _base_profile(self, prefix=None) -> dict:
        body = {"legal_name": "Test SAS", "address": "1 rue Test"}
        if prefix is not None:
            body["prefix_override"] = prefix
        return body

    def test_uppercase_alphanumeric_accepted(self):
        """FLW1 — valid prefix."""
        req = UpsertCompanyProfileRequest.model_validate(self._base_profile("FLW1"))
        assert req.prefix_override == "FLW1"

    def test_lowercase_rejected(self):
        """foo — lowercase violates ^[A-Z0-9]{1,8}$."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest.model_validate(self._base_profile("foo"))

    def test_slash_rejected(self):
        """FL/X — slash violates charset."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest.model_validate(self._base_profile("FL/X"))

    def test_space_rejected(self):
        """'FLW 1' — space violates charset."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest.model_validate(self._base_profile("FLW 1"))

    def test_max_8_chars_accepted(self):
        """12345678 — exactly 8 chars, valid."""
        req = UpsertCompanyProfileRequest.model_validate(self._base_profile("ABCD1234"))
        assert req.prefix_override == "ABCD1234"

    def test_9_chars_rejected(self):
        """9 chars — exceeds max_length=8."""
        with pytest.raises(ValidationError):
            UpsertCompanyProfileRequest.model_validate(self._base_profile("ABCDE1234"))

    def test_empty_prefix_none_accepted(self):
        """None prefix_override — optional field."""
        req = UpsertCompanyProfileRequest.model_validate(self._base_profile())
        assert req.prefix_override is None
