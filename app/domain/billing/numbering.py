"""Document number formatting helpers for the billing bounded context.

next_document_number(prefix_override, kind, year, sequence) -> str
    Returns the canonical document number string.

    Format:
        With prefix:    "{PREFIX}-{KIND_TOKEN}-{YEAR}-{SEQ:03d}"
        Without prefix: "{KIND_TOKEN}-{YEAR}-{SEQ:03d}"

    Examples:
        next_document_number("FLW", BillingDocumentKind.DEVIS, 2026, 7)
            → "FLW-DEV-2026-007"
        next_document_number("", BillingDocumentKind.FACTURE, 2026, 12)
            → "FAC-2026-012"

kind_to_token(kind) -> str
    Maps BillingDocumentKind → 3-letter token used in document numbers.
"""

from __future__ import annotations

from app.domain.billing.enums import BillingDocumentKind

_KIND_TOKENS: dict[BillingDocumentKind, str] = {
    BillingDocumentKind.DEVIS: "DEV",
    BillingDocumentKind.FACTURE: "FAC",
}


def kind_to_token(kind: BillingDocumentKind) -> str:
    """Return the 3-letter token for document numbering ("DEV" or "FAC")."""
    return _KIND_TOKENS[kind]


def next_document_number(
    prefix_override: str,
    kind: BillingDocumentKind,
    year: int,
    sequence: int,
) -> str:
    """Build the canonical document number string.

    Args:
        prefix_override: company prefix (e.g. "FLW"). Use "" for no prefix.
        kind:            document kind — DEVIS or FACTURE.
        year:            4-digit year (e.g. 2026).
        sequence:        1-based counter; zero-padded to 3 digits.

    Returns:
        Formatted document number string.
    """
    token = kind_to_token(kind)
    # Sequence is zero-padded to 3 digits. For sequence > 999 the format
    # grows to 4+ digits (e.g. "1000"), breaking the zero-pad symmetry.
    # This is intentional — unlimited growth is preferable to a hard cap
    # that would block document creation. The asymmetry is cosmetic only.
    seq_str = f"{sequence:03d}"
    if prefix_override:
        return f"{prefix_override}-{token}-{year}-{seq_str}"
    return f"{token}-{year}-{seq_str}"
