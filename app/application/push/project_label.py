"""The project label a push names: the same one the apps show."""

from __future__ import annotations


def project_label(project) -> str:
    """Name, or the site address when the name is blank (`project_display_label`, tolerant of a missing project)."""
    if project is None:
        return ""
    name = getattr(project, "name", None)
    name = name.strip() if isinstance(name, str) else ""
    if name:
        return name
    address = getattr(project, "address", None)
    return address.strip() if isinstance(address, str) else ""
