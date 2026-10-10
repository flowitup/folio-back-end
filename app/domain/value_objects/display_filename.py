"""Control-character rules for user-visible file names (documents, attachments).

Pure stdlib — zero Flask/SQLAlchemy/infra dependencies.

A stored name ends up in the Content-Disposition header of every download.
Werkzeug refuses a header value holding CR/LF, so a name with a line break
made the file undownloadable (500) until someone renamed it.
"""

from __future__ import annotations

import re

# C0 controls (incl. TAB, CR, LF, NUL) and DEL.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def has_control_chars(name: str) -> bool:
    """True when ``name`` holds a control character (line break, tab, NUL...)."""
    return _CONTROL_CHARS.search(name) is not None


def strip_control_chars(name: str) -> str:
    """Return ``name`` without its control characters."""
    return _CONTROL_CHARS.sub("", name)
