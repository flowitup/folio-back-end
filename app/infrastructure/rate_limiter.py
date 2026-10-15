"""Rate limiting infrastructure."""

import ipaddress
from typing import Optional

from flask import request
from flask_limiter import Limiter


def client_ip_bucket(address: Optional[str]) -> str:
    """Rate-limit bucket of a client address.

    An IPv4 address is its own bucket. An IPv6 client usually controls a whole /64 and can pick
    any address in it, so a per-address bucket is free to rotate: the bucket is the /64 network
    (an IPv4-mapped address maps to its IPv4). Anything unparseable is kept as is.
    """
    if not address:
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(ip), 64), strict=False))
    return str(ip)


def remote_address_bucket() -> str:
    """Key function: the bucket of ``request.remote_addr`` (after the trusted-proxy fix-up)."""
    return client_ip_bucket(request.remote_addr)


# storage_uri is omitted here so flask-limiter reads RATELIMIT_STORAGE_URI
# from the Flask app config at init_app() time (allows test overrides)
limiter = Limiter(
    key_func=remote_address_bucket,
    default_limits=["100 per minute"],
)
