"""Section headings of a billing document's lines, shared by the PDF and XLSX renderers.

A line's ``category`` is its section. A heading is printed where the section changes.
Lines with no section that follow a section get their own neutral heading: without
one they read as part of the section above them. Lines with no section at the top,
before any section, keep printing with no heading.
"""

from __future__ import annotations

from typing import Iterable, Optional

#: Heading of the lines with no section, once a section has been printed (documents are in French).
UNSECTIONED_HEADING = "Divers"


def section_headings(categories: Iterable[Optional[str]]) -> list[Optional[str]]:
    """For each line's category, the heading to print before that line, or None."""
    headings: list[Optional[str]] = []
    current: Optional[str] = None
    for category in categories:
        section = category or None
        if section != current and (section is not None or current is not None):
            headings.append(section if section is not None else UNSECTIONED_HEADING)
        else:
            headings.append(None)
        current = section
    return headings
