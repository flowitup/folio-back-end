"""Slug derivation for supplier names.

Pure stdlib (unicodedata, re) — zero Flask/SQLAlchemy/infra dependencies.
Mirrors the folding approach in library_category._fold.
"""

from __future__ import annotations

import re
import unicodedata

# Collapse runs of non-alphanumeric characters to a single dash.
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")

_MAX_SLUG_LEN = 100
_FALLBACK = "supplier"


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a supplier name.

    Steps:
    1. NFKD decomposition + ASCII encode/decode (strips accents/diacritics).
    2. Lowercase.
    3. Collapse runs of non-alphanumeric characters to a single dash.
    4. Strip leading/trailing dashes.
    5. Truncate to 100 characters (strip trailing dash after truncation).
    6. Fall back to "supplier" when the result is empty.

    Examples:
        "Leroy Merlin"    → "leroy-merlin"
        "Castorama Éco"   → "castorama-eco"
        "M&S Supplies!"   → "m-s-supplies"
        ""                → "supplier"
        "   "             → "supplier"
    """
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    lower = normalized.lower()
    dashed = _NON_ALNUM_RUN.sub("-", lower)
    stripped = dashed.strip("-")

    # Truncate then clean up any trailing dash introduced by the cut.
    truncated = stripped[:_MAX_SLUG_LEN].rstrip("-")

    return truncated if truncated else _FALLBACK
