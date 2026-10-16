"""SQL twin of `project_label` / `project_display_label`: name first, site address second."""

from __future__ import annotations

from sqlalchemy import func


def project_label_expr(project_model):
    """The project's name, or its site address when the name is blank (name-first, as the apps show it).

    `project_model` is the ProjectModel class or an alias of it.
    """
    return func.coalesce(
        func.nullif(func.trim(project_model.name), ""),
        func.nullif(func.trim(project_model.address), ""),
        project_model.name,
    )
