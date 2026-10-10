"""Browser auth cookies, set the same way by every route that issues tokens.

Flask-JWT-Extended sends session cookies unless told a lifetime (``JWT_SESSION_COOKIE`` defaults to
True): closing the browser signed the user out although the refresh token had days left. Each cookie
now lives as long as the token it carries, read from the token's own ``exp``.
"""

from __future__ import annotations

import time
from typing import Optional

from flask import Response
from flask_jwt_extended import decode_token, set_access_cookies, set_refresh_cookies

# A persistent refresh token (REFRESH_TOKEN_POLICY=persistent) never expires; its cookie gets the
# library's own non-session lifetime, one year (browsers cap cookies at about 400 days anyway).
NEVER_EXPIRING_COOKIE_MAX_AGE = 31_540_000


def _max_age(token: str) -> int:
    """Seconds until ``token`` expires, or a year for a token that never does."""
    exp = decode_token(token).get("exp")
    if exp is None:
        return NEVER_EXPIRING_COOKIE_MAX_AGE
    return max(0, int(exp - time.time()))


def set_auth_cookies(response: Response, access_token: str, refresh_token: Optional[str] = None) -> None:
    """Set the access cookie (and the refresh cookie when given), each for its token's lifetime."""
    set_access_cookies(response, access_token, max_age=_max_age(access_token))
    if refresh_token is not None:
        set_refresh_cookies(response, refresh_token, max_age=_max_age(refresh_token))
