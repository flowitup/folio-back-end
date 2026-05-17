"""Werkzeug-backed IFilenameSanitizer adapter.

Werkzeug's `secure_filename` strips path-traversal sequences, NUL bytes,
control characters, and normalizes Unicode. It is purely a string operation
with no Flask runtime dependency, but importing it from the application layer
violates the hexagonal "application depends on ports only" rule. This adapter
moves the dependency into the infrastructure layer where it belongs.
"""

from __future__ import annotations

from werkzeug.utils import secure_filename

from app.application.project_documents.ports import IFilenameSanitizer


class WerkzeugFilenameSanitizer(IFilenameSanitizer):
    """Default production sanitizer; delegates to `werkzeug.utils.secure_filename`."""

    def sanitize(self, filename: str) -> str:
        return secure_filename(filename)
