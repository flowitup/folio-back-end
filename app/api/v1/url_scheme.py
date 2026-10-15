"""http(s)-only handling of URL fields that clients display and open.

A stored ``javascript:``, ``data:``, ``tel:`` or app deep link is one tap away from running
in a viewer (the mobile library screen hands product_url straight to ``Linking.openURL``).
Only the scheme is looked at: some fields (a supplier product URL template) carry a
placeholder such as ``{ref}`` that is not a valid URL until it is filled in.
"""

from __future__ import annotations

import re

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_HOST_PORT = re.compile(r"^[a-z0-9.-]+:\d+(?:[/?#]|$)", re.IGNORECASE)
_BARE_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)


def normalize_http_url(value: object, *, any_bare_domain: bool = False) -> str | None:
    """The URL with an http(s) scheme, or None when it is blank or not an http(s) address.

    ``https://`` / ``http://`` values are kept as they are. A value without a scheme gets
    ``https://`` when it starts with ``www.`` (or, with ``any_bare_domain``, looks like a
    domain such as ``leroymerlin.fr/p/1``). Any other scheme, and anything else, is None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower().startswith(("http://", "https://")):
        return text
    if _SCHEME.match(text) and not _HOST_PORT.match(text):
        return None
    if text.lower().startswith("www.") or (any_bare_domain and _BARE_DOMAIN.match(text)):
        return f"https://{text}"
    return None


def require_http_url(value: object) -> object:
    """Validator for a field a client types or pastes: None and blank pass through (blank clears the
    field), a bare domain gets ``https://`` (the web app has always displayed those as links),
    any other scheme is refused."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return value
    normalized = normalize_http_url(value, any_bare_domain=True)
    if normalized is None:
        raise ValueError("URL must start with http:// or https://")
    return normalized
