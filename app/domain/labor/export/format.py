"""Pure formatting helpers for labor export output.

format_eur_fr   — Decimal → fr-FR currency string matching FE Intl.NumberFormat
slugify_project_name — project name → kebab-case filename-safe slug
"""

from __future__ import annotations

from decimal import Decimal


def format_eur_fr(value: Decimal | None) -> str:
    """Render a Decimal as a fr-FR EUR currency string.

    Mirrors FE: Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR' })
    Examples:
        200       → "200,00 €"
        1234.5    → "1 234,50 €"
        None      → "—"
    """
    if value is None:
        return "—"  # em dash
    # Format with thousands comma and 2 decimal places: "1,234.56"
    s = f"{float(value):,.2f}"
    # Convert to fr-FR notation: comma → thousand-sep space, period → decimal comma
    s = s.replace(",", "X").replace(".", ",").replace("X", " ")  # narrow no-break space
    return f"{s} €"  # non-breaking space before €


def slugify_project_name(name: str, fallback_id: str) -> str:
    """Convert a project name to a kebab-case filename slug (≤32 chars).

    Uses python-slugify for Unicode → ASCII transliteration.
    Falls back to the first 8 chars of fallback_id when:
      - the slug is empty, OR
      - the original name contains no Latin/digit characters (e.g. pure CJK, emoji)
        because python-slugify would romanize CJK to pinyin which is misleading.

    Vietnamese (Latin-Extended) and French (Latin-1) are kept as-is via slugify.
    """
    import unicodedata

    from slugify import slugify  # python-slugify; imported lazily to keep module fast at top-level

    # Check if name has any Latin-script or digit characters (categories Ll, Lu, Nd, Zs).
    # Vietnamese diacritics (ă, ơ, ư…) are Latin Extended → category Ll/Lu → pass.
    # CJK ideographs (工地) and emoji (🏗️) have no Latin category → fail → use fallback.
    has_latin_content = any(unicodedata.category(c) in ("Ll", "Lu", "Nd") for c in (name or ""))
    if not has_latin_content:
        return (fallback_id or "")[:8] or "project"

    slug = slugify(name, max_length=32, word_boundary=True, save_order=True)
    if not slug:
        slug = (fallback_id or "")[:8] or "project"
    return slug


if __name__ == "__main__":
    # Quick smoke-test for manual verification
    print(repr(format_eur_fr(Decimal("200"))))  # '200,00\xa0€'
    print(repr(format_eur_fr(Decimal("1234.5"))))  # '1\xa0234,50\xa0€'
    print(repr(format_eur_fr(Decimal("0"))))  # '0,00\xa0€'
    print(repr(format_eur_fr(None)))  # '—'
    print(repr(slugify_project_name("Downtown Office Tower", "abc")))
    print(repr(slugify_project_name("🏗️工地", "378bc41112345")))
