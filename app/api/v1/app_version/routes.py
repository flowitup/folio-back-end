"""GET /app-version — tells the mobile app whether it must update before use."""

from flask import current_app, jsonify

from app.api.openapi import openapi_doc
from app.api.v1.app_version import app_version_bp
from app.infrastructure.rate_limiter import limiter


@app_version_bp.route("/app-version", methods=["GET"])
@openapi_doc(summary="Minimum mobile app version still allowed to run", tags=["app"])
@limiter.limit("60 per minute")
def get_app_version():
    """Public (no sign-in): the app asks before the login screen, per platform.

    An empty minimum means no gate. Raising ``APP_MIN_VERSION_*`` in the server
    environment is how an old build is forced to update.
    """
    cfg = current_app.config
    return jsonify(
        {
            "ios": {
                "min_version": cfg.get("APP_MIN_VERSION_IOS", ""),
                "update_url": cfg.get("APP_UPDATE_URL_IOS", ""),
            },
            "android": {
                "min_version": cfg.get("APP_MIN_VERSION_ANDROID", ""),
                "update_url": cfg.get("APP_UPDATE_URL_ANDROID", ""),
            },
        }
    )
