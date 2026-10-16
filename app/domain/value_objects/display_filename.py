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
# What breaks a quoted header parameter, plus the "?" that encode("ascii", "replace") leaves.
_QUOTED_UNSAFE = re.compile(r'["\\?]')


# Longest display name accepted (filesystems cap a name at 255). A longer one made the
# download's Content-Disposition header so large that proxies cut the response.
MAX_DISPLAY_FILENAME_LENGTH = 255


def is_too_long(name: str) -> bool:
    """True when ``name`` (stripped) is longer than MAX_DISPLAY_FILENAME_LENGTH characters."""
    return len((name or "").strip()) > MAX_DISPLAY_FILENAME_LENGTH


def has_control_chars(name: str) -> bool:
    """True when ``name`` holds a control character (line break, tab, NUL...)."""
    return _CONTROL_CHARS.search(name) is not None


def strip_control_chars(name: str) -> str:
    """Return ``name`` without its control characters."""
    return _CONTROL_CHARS.sub("", name)


def quoted_ascii_filename(name: str) -> str:
    """``name`` as the content of a quoted ``filename="..."`` parameter: ASCII, no control characters.

    A double quote or backslash would end the quoted string early (or escape its end), so they,
    like any non-ASCII character, become an underscore; the real name travels in ``filename*``.
    """
    return _QUOTED_UNSAFE.sub("_", strip_control_chars(name).encode("ascii", "replace").decode("ascii"))
