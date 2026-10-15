"""http(s)-only handling of URL fields that clients display and open.

A stored ``javascript:``, ``data:``, ``tel:`` or app deep link is one tap away from running
in a viewer (the mobile library screen hands product_url straight to ``Linking.openURL``).
Only the scheme is looked at: some fields (a supplier product URL template) carry a
placeholder such as ``{ref}`` that is not a valid URL until it is filled in.
"""

from __future__ import annotations

import re

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
# What a browser ignores inside or before a URL's scheme: ``java\tscript:`` is still ``javascript:``.
_IGNORED = re.compile(r"[\x00-\x20\x7f-\x9f]")
_HOST_PORT = re.compile(r"^[a-z0-9.-]+:\d+(?:[/?#]|$)", re.IGNORECASE)
_BARE_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)


def normalize_http_url(value: object, *, any_bare_domain: bool = False) -> str | None:
    """The URL with an http(s) scheme, or None when it is blank or not an http(s) address.

    ``https://`` / ``http://`` values are kept as they are. A value without a scheme gets
    ``https://`` when it starts with ``www.`` (or, with ``any_bare_domain``, looks like a
    domain such as ``leroymerlin.fr/p/1``). Any other scheme (control characters and whitespace are
    ignored when looking for it), and anything else, is None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower().startswith(("http://", "https://")):
        return text
    probe = _IGNORED.sub("", text)
    if _SCHEME.match(probe) and not _HOST_PORT.match(probe):
        return None
    if text.lower().startswith("www.") or (any_bare_domain and _BARE_DOMAIN.match(text)):
        return f"https://{text}"
    return None


def lenient_http_url(max_length: int):
    """Before-validator for a field a client types or pastes: a usable http(s) address, else None.

    Like the library import, it never refuses a value: a bare domain or ``www.`` address gets
    ``https://`` and anything else (free text such as a stored "Leroy Merlin", a ``javascript:``
    or ``data:`` link) becomes None, so a form that re-sends an old stored value can still save.
    Blank and None clear the field. A value that is not a string is left for the field to refuse.
    """

    def validate(v: object) -> object:
        if not isinstance(v, str):
            return v
        url = normalize_http_url(v, any_bare_domain=True)
        # The "https://" prefix alone pushed a value that fitted over the limit.
        if url is not None and len(url) > max_length >= len(v.strip()):
            return None
        return url

    return validate
