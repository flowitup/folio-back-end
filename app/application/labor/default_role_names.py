"""Names of the two default labor roles every company is seeded with, per UI language.

The roles are identified by their stable ``slug`` (``tho_chinh`` / ``tho_phu``); clients
translate a seed role by slug as long as its stored name is still one of the labels below
(i.e. the company has not renamed it). Older companies were all seeded in Vietnamese, newer
ones in their creator's language, so every label below counts as "the default name".

Keep in sync with the clients: web ``src/lib/utils/default-role-names.ts`` and mobile
``src/lib/labor/labor-role-label.ts``.
"""

from __future__ import annotations

import unicodedata

SUPPORTED_LOCALES: tuple[str, ...] = ("fr", "en", "vi")
# Companies created before seeding was localized, and callers that name no language.
DEFAULT_LOCALE = "vi"

# slug -> (colour, {locale: label})
DEFAULT_ROLE_LABELS: dict[str, tuple[str, dict[str, str]]] = {
    "tho_chinh": (
        "#3B82F6",
        {"vi": "Thợ chính", "fr": "Ouvrier principal", "en": "Lead worker"},
    ),
    "tho_phu": (
        "#10B981",
        {"vi": "Thợ phụ", "fr": "Ouvrier auxiliaire", "en": "Assistant worker"},
    ),
}


def _fold(name: str) -> str:
    """Compare names composed, trimmed and case-insensitively (like the uniqueness check)."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def normalize_locale(raw: str | None) -> str | None:
    """Return ``fr`` / ``en`` / ``vi`` for a locale tag such as ``fr-FR``, else None."""
    if not raw:
        return None
    primary = raw.strip().replace("_", "-").split("-", 1)[0].lower()
    return primary if primary in SUPPORTED_LOCALES else None


def locale_from_accept_language(header: str | None) -> str | None:
    """Best supported language of an ``Accept-Language`` header (highest q first), else None."""
    if not header:
        return None
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                quality = 0.0
        locale = normalize_locale(tag)
        if locale is not None and quality > 0:
            candidates.append((-quality, index, locale))
    return sorted(candidates)[0][2] if candidates else None


def default_roles_for(locale: str | None) -> list[tuple[str, str, str]]:
    """``(name, colour, slug)`` of the default roles, named in ``locale`` (Vietnamese by default)."""
    lang = normalize_locale(locale) or DEFAULT_LOCALE
    return [(labels[lang], color, slug) for slug, (color, labels) in DEFAULT_ROLE_LABELS.items()]


def is_default_name(slug: str | None, name: str | None) -> bool:
    """True when ``name`` is one of the default labels of seed role ``slug`` (not renamed)."""
    if not slug or not name or slug not in DEFAULT_ROLE_LABELS:
        return False
    folded = _fold(name)
    return any(_fold(label) == folded for label in DEFAULT_ROLE_LABELS[slug][1].values())


def default_slug_for_name(name: str) -> str | None:
    """The seed slug whose label (in any language) ``name`` is, else None."""
    folded = _fold(name)
    for slug, (_color, labels) in DEFAULT_ROLE_LABELS.items():
        if any(_fold(label) == folded for label in labels.values()):
            return slug
    return None
