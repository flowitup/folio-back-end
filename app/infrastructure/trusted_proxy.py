"""Let the API see the real caller's address when it runs behind a reverse proxy.

Flask reads the client address from the socket peer, which behind a proxy is the proxy
itself: in production every request reaches the container through cloudflared (mobile and
direct API calls) or through the Next.js server actions (the web app), so without this the
whole deployment shares one address. Everything keyed on that address — flask-limiter's
per-route limits and its global default — then throttles all users together instead of one
at a time.

``TRUSTED_PROXY_HOPS`` says how many proxies sit in front of the API and may therefore speak
for the client through ``X-Forwarded-For``. Werkzeug counts from the right, which matches how
Cloudflare and cloudflared build that header today: each hop *appends* the address it saw, so
the last entry is the one the nearest trusted proxy observed and everything to its left is
whatever the caller claimed. A proxy that prepends instead would invert that, so the hop count
is only ever right for a topology someone has actually checked. It is 0 by default: a deployment that is *not*
behind a proxy keeps the socket peer, so a forged header cannot move someone else's requests
into another bucket. Only the address is taken from the header — scheme, host and port stay
as the server sees them, since nothing here depends on them.
"""

from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix


def apply_trusted_proxy(app: Flask) -> None:
    """Honour ``X-Forwarded-For`` from ``TRUSTED_PROXY_HOPS`` proxies, or nothing when it is 0."""
    hops = int(app.config.get("TRUSTED_PROXY_HOPS", 0) or 0)
    if hops <= 0:
        return
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=0, x_host=0, x_port=0, x_prefix=0)
