"""Pure formatting helpers for labor export output.

format_eur_fr   — Decimal → fr-FR currency string matching FE Intl.NumberFormat
format_unit_price_eur_fr — the same, keeping a unit price's own decimals (15,015 €)
format_decimal_fr — quantity / rate → fr-FR number with a decimal comma
format_generated_at — the export's UTC timestamp → "dd/mm/YYYY HH:MM" on the Paris clock
slugify_project_name — project name → kebab-case filename-safe slug
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.time import business_now


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
    # Round half-up in Decimal (a float cast loses digits on large amounts and
    # rounds 0.125 down), then format with thousands comma and 2 dp: "1,234.56"
    cents = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{cents:,.2f}"
    # Convert to fr-FR notation: comma → thousand-sep space, period → decimal comma
    s = s.replace(",", "X").replace(".", ",").replace("X", " ")  # narrow no-break space
    return f"{s} €"  # non-breaking space before €


def format_unit_price_eur_fr(value: Decimal | None) -> str:
    """Render a unit price like format_eur_fr, but with every decimal it carries (at least 2).

    A line's amount is quantity × unit price. Rounded to the cent, 15.015 would print as
    15,02 € and 3,333 × 15,02 € would not give the 50,04 € printed next to it.
    Examples:
        15.015    → "15,015 €"
        1234.565  → "1 234,565 €"
        200       → "200,00 €"
    """
    if value is None:
        return "—"
    d = Decimal(str(value)).normalize()
    if d.as_tuple().exponent >= -2:
        return format_eur_fr(d)
    s = f"{d:,f}".replace(",", "X").replace(".", ",").replace("X", "\u00a0")  # same separators
    return f"{s}\u00a0€"


def format_decimal_fr(value: Decimal | float | int | None) -> str:
    """Render a quantity or rate the fr-FR way, without trailing zeros.

    Decimal("1.5") → "1,5", Decimal("5.50") → "5,5", 12.0 → "12", 1234.5 → "1 234,5".
    Same separators as format_eur_fr, so a line reads consistently next to its amounts.
    """
    if value is None:
        return "—"
    d = Decimal(str(value))
    if d == d.to_integral_value():
        d = d.quantize(Decimal(1))
    else:
        d = d.normalize()
    int_part, _, frac = format(d, "f").partition(".")
    grouped = f"{int(int_part):,}".replace(",", " ")
    if int_part.startswith("-") and not grouped.startswith("-"):
        grouped = "-" + grouped
    return f"{grouped},{frac}" if frac else grouped


def format_generated_at(at: datetime, with_time: bool = True) -> str:
    """When an export was made, as its reader's wall clock: "09/10/2026 20:16" (Europe/Paris).

    The timestamp is recorded in UTC; printed as is it read two hours early with no zone.
    """
    return business_now(at).strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")


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


def slugify_worker_name(name: str, fallback_id: str) -> str:
    """Convert a worker name to a kebab-case filename slug (≤32 chars).

    Same algorithm as slugify_project_name; separate function to make intent
    explicit at call sites.
    Falls back to the first 8 chars of fallback_id when the slug would be empty
    or the name has no Latin/digit characters.
    """
    import unicodedata

    from slugify import slugify

    has_latin_content = any(unicodedata.category(c) in ("Ll", "Lu", "Nd") for c in (name or ""))
    if not has_latin_content:
        return (fallback_id or "")[:8] or "worker"

    slug = slugify(name, max_length=32, word_boundary=True, save_order=True)
    if not slug:
        slug = (fallback_id or "")[:8] or "worker"
    return slug


if __name__ == "__main__":
    # Quick smoke-test for manual verification
    print(repr(format_eur_fr(Decimal("200"))))  # '200,00\xa0€'
    print(repr(format_eur_fr(Decimal("1234.5"))))  # '1\xa0234,50\xa0€'
    print(repr(format_eur_fr(Decimal("0"))))  # '0,00\xa0€'
    print(repr(format_eur_fr(None)))  # '—'
    print(repr(slugify_project_name("Downtown Office Tower", "abc")))
    print(repr(slugify_project_name("🏗️工地", "378bc41112345")))
