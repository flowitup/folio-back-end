"""Werkzeug-backed IFilenameSanitizer adapter.

Werkzeug's `secure_filename` strips path-traversal sequences, NUL bytes,
control characters, and normalizes Unicode. It is purely a string operation
with no Flask runtime dependency, but importing it from the application layer
violates the hexagonal "application depends on ports only" rule. This adapter
moves the dependency into the infrastructure layer where it belongs.
"""

from __future__ import annotations

import os
import re

from werkzeug.utils import secure_filename

from app.application.project_documents.ports import IFilenameSanitizer

# An extension worth keeping: a dot then ASCII letters/digits only.
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,16}$")


class WerkzeugFilenameSanitizer(IFilenameSanitizer):
    """Default production sanitizer built on `werkzeug.utils.secure_filename`.

    `secure_filename` keeps only ASCII, so a name written entirely in another
    script ('Смета.pdf', '图纸.pdf', 'Đđ.pdf') used to collapse to 'pdf' and lose
    its extension, which every upload then refused as an unsupported type. The
    stem and the extension are therefore sanitized separately: an ASCII
    extension is always kept, and an empty stem becomes 'file'.
    """

    def sanitize(self, filename: str) -> str:
        base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        stem, ext = os.path.splitext(base)
        if not _SAFE_EXTENSION.match(ext):
            return secure_filename(filename)
        safe_stem = secure_filename(stem) or "file"
        return f"{safe_stem}{ext}"
