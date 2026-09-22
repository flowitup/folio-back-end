"""Pydantic models the AI pipeline validates provider JSON against.

These are the shapes `VisionLlmPort.chat_json` validates DeepSeek's output into (see
`app.application.assistant.ports`), plus the small dataclasses `Router` (S0) returns.
Kept dependency-free of any provider SDK — only pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.assistant.scope import allowed_classes_for
from app.domain.entities.chat_message import ChannelRef

# ---------------------------------------------------------------------------
# S1 extraction (Feature B/C: invoices and receipts)
# ---------------------------------------------------------------------------


class Line(BaseModel):
    label: str
    qty: Optional[float] = None
    unit_price_ht: Optional[float] = None
    total_ttc: Optional[float] = None


class Invoice(BaseModel):
    """Output of S1 (`extract.py`) for an invoice or a receipt photo/PDF page."""

    merchant: str
    store_city: Optional[str] = None
    invoice_number: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD
    reference_field: Optional[str] = None
    delivery_address: Optional[str] = None
    total_ht: Optional[float] = None
    total_tva: Optional[float] = None
    total_ttc: float
    tva_rates: list[float] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    payment_method: Optional[str] = None
    readability: float


# ---------------------------------------------------------------------------
# Feature A (material identification — phase 03/04 wires this)
# ---------------------------------------------------------------------------


class MaterialIdent(BaseModel):
    """Output of A1: DeepSeek vision reading a material photo."""

    brand: Optional[str] = None
    name: str
    reference: Optional[str] = None
    ean: Optional[str] = None
    category: str
    specs: Optional[str] = None
    search_queries: list[str] = Field(default_factory=list)
    confidence: float


class Product(BaseModel):
    """Output of A4: a web hit enriched into a catalogue-ready product."""

    name: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    ean: Optional[str] = None
    price_ttc: Optional[float] = None
    unit: Optional[str] = None
    image_url: Optional[str] = None
    source_url: str


# ---------------------------------------------------------------------------
# Feature B (invoice fetch — phase 04 wires this)
# ---------------------------------------------------------------------------


class ProductCandidate(BaseModel):
    """One product page the browser agent found for a `find_product` job (feature A)."""

    url: str
    title: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    ean: Optional[str] = None
    price_ttc: Optional[float] = None
    unit: Optional[str] = None
    image_url: Optional[str] = None
    merchant: str


class ProductSearchResult(BaseModel):
    """What the browser-use worker reports back for a `find_product` job.

    "failed" is not something the agent itself ever reports (its structured output
    schema only knows the plan's 3 outcomes) — it is what
    ``app.infrastructure.browser_worker.agent`` falls back to when the agent run itself
    raised, mirroring ``FetchResult`` below.
    """

    status: Literal["done", "not_found", "blocked", "failed"]
    candidates: list[ProductCandidate] = Field(default_factory=list)
    message: Optional[str] = None


class FetchResult(BaseModel):
    """What the browser-use worker reports back for a `fetch_invoice` job.

    "failed" is not something the agent itself ever reports (browser-use's structured
    output schema only knows about the plan's 4 outcomes) — it is what
    ``app.infrastructure.browser_worker.agent`` falls back to when the agent run itself
    raised (missing dependency, crashed browser, unparseable output, ...), so the poll
    loop always has a valid ``FetchResult`` to write back to ``assistant_jobs``.
    """

    status: Literal["done", "not_found", "not_ready", "blocked", "failed"]
    pdf_path: Optional[str] = None
    message: Optional[str] = None


class AmountDate(BaseModel):
    """Amount/date parsed out of a chat utterance ("79,54e", "hier", "21/09")."""

    amount_ttc: Optional[float] = None
    date: Optional[str] = None  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# Phase 04 — tasks handler (catalogue 3.1: create a task from a chat utterance)
# ---------------------------------------------------------------------------


class TaskDraft(BaseModel):
    """Title/due date parsed by DeepSeek out of a "create a task" chat utterance."""

    title: Optional[str] = None
    due_date: Optional[str] = None  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# Router (S0)
# ---------------------------------------------------------------------------

#: The intents Router.route() can return — shared by router.py and reply.py
#: (clarifying-choice labels) so the two never drift apart.
INTENTS: tuple[str, ...] = (
    "identify_material",
    "import_ticket",
    "fetch_invoice",
    "find_equipment",
    "move_equipment",
    "question",
    "chit_chat",
    # Phase 03 — confidential-class questions (D17): refused outside the admin channel.
    "ask_project_income",
    "ask_salary",
    "ask_own_salary",
    # Phase 04 — labor/tasks handlers on channels (catalogue 2.1-2.3, 3.1-3.2).
    "ask_roster",
    "log_attendance",
    "validate_attendance",
    "create_task",
    "ask_tasks",
    # Phase 03/04 — admin-channel supervision ("who asked what this week").
    "ask_audit",
    "ask_unpaid_invoices",
)

#: Merchants Jev is asked to recognise for `fetch_invoice` (S0 `merchant` question).
#: Kept in lockstep with `app.infrastructure.browser_worker.merchants.MERCHANT_DOMAINS`
#: (the browser allowlist) — a merchant absent here can never be routed to even though
#: the agent is technically allowed to visit its domain.
MERCHANTS: tuple[str, ...] = (
    "leroymerlin",
    "pointp",
    "castorama",
    "bricodepot",
    "gedimat",
    "technomat",
    "manomano",
)


class RouterDecision(BaseModel):
    """S0 output: intent + merchant + project hint + write flag, each with Jev confidence."""

    intent: str
    intent_confidence: float
    intent_probabilities: dict[str, float] = Field(default_factory=dict)
    merchant: Optional[str] = None
    merchant_confidence: float = 0.0
    project_hint: Optional[str] = None
    project_hint_confidence: float = 0.0
    is_write: float = 0.0


# ---------------------------------------------------------------------------
# Channel scope (phase 02: dispatch now happens in company/project/admin channels,
# never just a private per-user conversation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelScope:
    """Who is asking, and from where — threaded from ``AssistantService`` down to
    ``FeatureHandlersPort`` so a feature can redact confidential classes and gate tool
    access by audience (D17).

    ``company_id`` is the channel's own id for ``"company"``/``"admin"`` kinds, and the
    owning company of the project for a ``"project"`` channel (resolved via
    ``ProjectCompanyReaderPort`` — ``None`` when the project has no company yet).

    ``allowed_classes`` is always computed from ``is_admin_channel`` (never passed by a
    caller) — every confidential class (``finance_company``, ``payroll``) in an admin
    channel, none otherwise. See ``app.application.assistant.scope``.
    """

    kind: str
    company_id: Optional[UUID]
    project_id: Optional[UUID]
    is_admin_channel: bool
    asker_id: UUID
    allowed_classes: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_classes", allowed_classes_for(self.is_admin_channel))

    @staticmethod
    def for_channel(
        channel: ChannelRef, *, project_company_id: "Callable[[UUID], Optional[UUID]]", asker_id: UUID
    ) -> "ChannelScope":
        """Build the scope a bare ``ChannelRef`` resolves to.

        Shared by ``AssistantService._resolve_scope`` (the synchronous dispatch path,
        which always has the asker's own request to build a scope from) and an async
        job's ``on_result`` (feature A/B), which only ever has the job's stored
        ``channel_key`` to go on — never the original request's own scope object, since
        the browser worker may finish long after that request returned.
        """
        if channel.kind == "project":
            return ChannelScope(
                kind="project",
                company_id=project_company_id(channel.id),
                project_id=channel.id,
                is_admin_channel=False,
                asker_id=asker_id,
            )
        if channel.kind == "admin":
            return ChannelScope(
                kind="admin", company_id=channel.id, project_id=None, is_admin_channel=True, asker_id=asker_id
            )
        return ChannelScope(
            kind="company", company_id=channel.id, project_id=None, is_admin_channel=False, asker_id=asker_id
        )

    @property
    def channel(self) -> ChannelRef:
        """The ``ChannelRef`` this scope was resolved from — where replies belong."""
        if self.kind == "project":
            assert self.project_id is not None, "a project scope always carries its project_id"
            return ChannelRef(kind="project", id=self.project_id)
        assert self.company_id is not None, "a company/admin scope always carries its company_id"
        return ChannelRef(kind=self.kind, id=self.company_id)
