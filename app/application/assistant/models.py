"""Pydantic models the AI pipeline validates provider JSON against.

These are the shapes `VisionLlmPort.chat_json` validates DeepSeek's output into (see
`app.application.assistant.ports`), plus the small dataclasses `Router` (S0) returns.
Kept dependency-free of any provider SDK — only pydantic.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

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
# Router (S0)
# ---------------------------------------------------------------------------

#: The seven intents Router.route() can return — shared by router.py and reply.py
#: (clarifying-choice labels) so the two never drift apart.
INTENTS: tuple[str, ...] = (
    "identify_material",
    "import_ticket",
    "fetch_invoice",
    "find_equipment",
    "move_equipment",
    "question",
    "chit_chat",
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
