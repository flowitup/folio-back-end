"""S1 — extraction: DeepSeek vision reads an invoice/receipt photo or PDF page into JSON.

The system prompt is a byte-identical module constant (plan hard rule #4 — DeepSeek's
prompt cache only hits on an exact match, 50x cheaper). `extract_invoice` itself is thin:
`VisionLlmPort.chat_json` already does the pydantic validation and the one retry at
temperature 0.2 (see `app.infrastructure.ai.deepseek_client`); this module only owns the
prompt text, the PDF -> image conversion, and the readability gate.

Consumed by feature C (ticket import) and feature B (fetched invoice), both phase 03/04 —
this phase only ships the pipeline stage itself plus its tests (item 9 of the phase file).
"""

from __future__ import annotations

import io

from app.application.assistant.gate import readability_ok
from app.application.assistant.models import Invoice
from app.application.assistant.ports import VisionLlmPort

# Keep byte-identical across every call — DeepSeek's prompt-cache hit price is 50x the
# cache-miss price, and it only hits on an exact match.
S1_SYSTEM_PROMPT_FR = (
    "Tu lis des factures et tickets de caisse BTP français. Réponds uniquement avec un JSON aux champs :\n"
    "merchant, store_city, invoice_number, date (YYYY-MM-DD), reference_field, delivery_address, total_ht, "
    "total_tva,\n"
    "total_ttc, tva_rates, lines[{label, qty, unit_price_ht, total_ttc}], payment_method, readability (0-1).\n"
    "Champs absents → null. Montants en nombre (79.54). reference_field = référence chantier/commande imprimée. "
    "N'invente rien."
)

_USER_TEXT = "Extrait la facture."

#: pdf2image renders at this resolution — matches plan section 3 ("150 dpi, page 1 (+2)").
_PDF_DPI = 150
#: Page 1 always; page 2 only when the document has more than one page (a second page
#: sometimes carries the line-item detail a receipt's first page truncates).
_MAX_PDF_PAGES = 2


def pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    """Render PDF pages 1(+2) to JPEG bytes at 150 dpi (`pdf2image`, needs poppler-utils)."""
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(pdf_bytes, dpi=_PDF_DPI, first_page=1, last_page=_MAX_PDF_PAGES)
    images: list[bytes] = []
    for page in pages:
        buffer = io.BytesIO()
        page.convert("RGB").save(buffer, format="JPEG", quality=90)
        images.append(buffer.getvalue())
    return images


def extract_invoice(vision: VisionLlmPort, images: list[bytes]) -> Invoice:
    """Run S1 on one or more page images (already JPEG-ish bytes; `chat_json` handles resizing)."""
    return vision.chat_json(
        system=S1_SYSTEM_PROMPT_FR, user_text=_USER_TEXT, images=images, model_cls=Invoice, temperature=0.0
    )


def is_readable(invoice: Invoice) -> bool:
    """False → the caller should reply "retake the photo" instead of proceeding to S2/S3."""
    return readability_ok(invoice.readability)
