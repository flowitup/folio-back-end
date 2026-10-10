"""App sections a company may hide from its members' navigation (mobile app)."""

from __future__ import annotations

# Keys match the mobile navigation: Menu areas (billing, library, inventory) and the
# project sections reached from the Menu.
HIDEABLE_SECTIONS: tuple[str, ...] = (
    "billing",
    "library",
    "inventory",
    "documents",
    "photos",
    "notes",
    "salaries",
    "chiffrage",
    "analyses",
)


def normalize_hidden_sections(values: list[str]) -> tuple[str, ...]:
    """Validate and de-duplicate, keeping the canonical order. Raises ValueError on an unknown key."""
    unknown = sorted(set(values) - set(HIDEABLE_SECTIONS))
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(unknown)}")
    wanted = set(values)
    return tuple(key for key in HIDEABLE_SECTIONS if key in wanted)
