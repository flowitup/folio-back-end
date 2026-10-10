"""RenderBillingDocumentPdfUseCase — render a billing document to PDF bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.billing._helpers import _assert_billing_doc_access, _linked_project
from app.application.billing.ports import (
    BillingDocumentPdfRendererPort,
    BillingDocumentRepositoryPort,
    ProjectReadPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.billing.exceptions import BillingDocumentNotFoundError


@dataclass(frozen=True)
class RenderPdfResult:
    """PDF bytes plus a suggested download filename."""

    content: bytes
    filename: str


class RenderBillingDocumentPdfUseCase:
    """Render a billing document to PDF bytes.

    Ownership check enforced before rendering.
    Filename format: ``{document_number}.pdf`` (e.g. ``FAC-2026-001.pdf``).
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        pdf_renderer: BillingDocumentPdfRendererPort,
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
        project_repo: Optional[ProjectReadPort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._pdf_renderer = pdf_renderer
        self._access_repo = access_repo
        self._project_repo = project_repo

    def execute(self, doc_id: UUID, user_id: UUID) -> RenderPdfResult:
        doc = self._doc_repo.find_by_id(doc_id)
        if doc is None:
            raise BillingDocumentNotFoundError(doc_id)
        _assert_billing_doc_access(doc, user_id, self._access_repo)

        pdf_bytes = self._pdf_renderer.render(doc, project=_linked_project(self._project_repo, doc))
        filename = f"{doc.document_number}.pdf"
        return RenderPdfResult(content=pdf_bytes, filename=filename)
