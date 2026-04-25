"""Rate limiting infrastructure."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# storage_uri is omitted here so flask-limiter reads RATELIMIT_STORAGE_URI
# from the Flask app config at init_app() time (allows test overrides)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per minute"],
)
