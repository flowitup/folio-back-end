"""Jinja2-based email template renderer."""

import jinja2

# Hardcoded subjects per (template_name, locale).
# Use Python str.format(**context) after lookup.
SUBJECTS: dict[tuple[str, str], str] = {
    ("invite", "en"): "You're invited to join {project_name} on Folio",
    ("invite", "fr"): "Vous êtes invité à rejoindre {project_name} sur Folio",
    ("invite", "vi"): "Bạn được mời tham gia {project_name} trên Folio",
    ("added_to_project", "en"): "You've been added to {project_name} on Folio",
    ("added_to_project", "fr"): "Vous avez été ajouté à {project_name} sur Folio",
    ("added_to_project", "vi"): "Bạn đã được thêm vào {project_name} trên Folio",
}


class EmailRenderer:
    """Render Jinja2 email templates into (subject, text_body, html_body) tuples."""

    def __init__(self, templates_dir: str) -> None:
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(templates_dir),
            autoescape=jinja2.select_autoescape(["html"]),
            undefined=jinja2.StrictUndefined,
        )

    def render(
        self,
        template_name: str,
        locale: str,
        context: dict,
    ) -> tuple[str, str, str]:
        """
        Render a template for the given locale.

        Returns:
            (subject, text_body, html_body)

        Raises:
            KeyError: if (template_name, locale) has no subject mapping.
            jinja2.TemplateNotFound: if template files are missing.
        """
        subject_tpl = SUBJECTS[(template_name, locale)]
        # Format subject with plain (unescaped) context values so we don't get
        # HTML entities in the subject line.
        subject = subject_tpl.format(**context)

        txt_tmpl = self._env.get_template(f"{template_name}.{locale}.txt")
        html_tmpl = self._env.get_template(f"{template_name}.{locale}.html")

        text_body = txt_tmpl.render(**context)
        html_body = html_tmpl.render(**context)

        return subject, text_body, html_body
