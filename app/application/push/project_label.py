"""The project label a push names: the same one the apps show."""

from __future__ import annotations


def project_label(project) -> str:
    """Site address, or the name when there is none (`project_display_label`, tolerant of a missing project)."""
    if project is None:
        return ""
    address = getattr(project, "address", None)
    address = address.strip() if isinstance(address, str) else ""
    return address or getattr(project, "name", "") or ""
