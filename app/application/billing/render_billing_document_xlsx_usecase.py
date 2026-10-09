"""RenderBillingDocumentXlsxUseCase — render a billing doc to XLSX bytes.

Mirrors RenderBillingDocumentPdfUseCase: load by id, assert ownership, delegate
to the renderer port. Filename: ``{document_number}.xlsx``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.billing._helpers import _assert_billing_doc_access, _linked_project
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingDocumentXlsxRendererPort,
    ProjectReadPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.billing.exceptions import BillingDocumentNotFoundError


@dataclass(frozen=True)
class RenderXlsxResult:
    """XLSX bytes plus a suggested download filename."""

    content: bytes
    filename: str


class RenderBillingDocumentXlsxUseCase:
    """Render a billing document to XLSX bytes.

    Ownership check enforced before rendering.
    Filename format: ``{document_number}.xlsx`` (e.g. ``FAC-2026-001.xlsx``).
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        xlsx_renderer: BillingDocumentXlsxRendererPort,
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
        project_repo: Optional[ProjectReadPort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._xlsx_renderer = xlsx_renderer
        self._access_repo = access_repo
        self._project_repo = project_repo

    def execute(self, doc_id: UUID, user_id: UUID) -> RenderXlsxResult:
        doc = self._doc_repo.find_by_id(doc_id)
        if doc is None:
            raise BillingDocumentNotFoundError(doc_id)
        _assert_billing_doc_access(doc, user_id, self._access_repo)

        xlsx_bytes = self._xlsx_renderer.render(doc, project=_linked_project(self._project_repo, doc))
        # Replace any path-unsafe chars in the doc number when forming filename.
        safe_number = doc.document_number.replace("/", "-").replace("\\", "-")
        filename = f"{safe_number}.xlsx"
        return RenderXlsxResult(content=xlsx_bytes, filename=filename)
