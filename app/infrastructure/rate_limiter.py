"""Rate limiting infrastructure."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per minute"],
    storage_uri=Config.RATELIMIT_STORAGE_URL,
)
