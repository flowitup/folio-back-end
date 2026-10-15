"""
Application Configuration

This module reads and validates environment variables for the application.
Configuration follows the 12-factor app methodology.
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def get_env(key: str, default: Optional[str] = None, required: bool = False) -> str:
    """
    Get an environment variable with optional default and required validation.

    Args:
        key: Environment variable name
        default: Default value if not set
        required: If True, raises ValueError when not set and no default

    Returns:
        Environment variable value

    Raises:
        ValueError: If required and not set
    """
    value = os.getenv(key, default)
    if required and value is None:
        raise ValueError(f"Required environment variable '{key}' is not set")
    return value or ""


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Database
    DATABASE_URL: str = get_env("DATABASE_URL", default="sqlite:///dev.db")

    # Security
    SECRET_KEY: str = get_env("SECRET_KEY", default="dev-secret-key-change-in-production")

    # Redis / Queue
    REDIS_URL: str = get_env("REDIS_URL", default="redis://localhost:6379/0")

    # Email configuration
    # EMAIL_PROVIDER: 'resend' | 'inmemory' | 'smtp'
    EMAIL_PROVIDER: str = get_env("EMAIL_PROVIDER", default="smtp")
    SMTP_HOST: str = get_env("SMTP_HOST", default="localhost")
    SMTP_PORT: int = int(get_env("SMTP_PORT", default="587"))
    SMTP_USER: str = get_env("SMTP_USER", default="")
    SMTP_PASS: str = get_env("SMTP_PASS", default="")
    SMTP_USE_TLS: bool = get_env("SMTP_USE_TLS", default="true").lower() == "true"
    # Resend API settings (used when EMAIL_PROVIDER=resend)
    RESEND_API_KEY: str = get_env("RESEND_API_KEY", default="")
    FROM_EMAIL: str = get_env("FROM_EMAIL", default="")
    # Base URL of the frontend app (used in invitation links)
    APP_BASE_URL: str = get_env("APP_BASE_URL", default="http://localhost:3000")

    # Application settings
    DEBUG: bool = get_env("FLASK_DEBUG", default="false").lower() == "true"
    TESTING: bool = False

    # JWT Configuration
    JWT_SECRET_KEY: str = get_env("JWT_SECRET_KEY", default="dev-jwt-secret-change-in-production")
    JWT_ACCESS_TOKEN_EXPIRES: timedelta = timedelta(days=7)
    JWT_REFRESH_TOKEN_EXPIRES: timedelta = timedelta(days=7)
    JWT_TOKEN_LOCATION: tuple = ("headers", "cookies")
    # JWT cookie security — secure-by-default everywhere.
    #   Production (FLASK_ENV=production)        : Secure=True,  CSRF=True,  SameSite=Strict.
    #   Non-prod default                         : Secure=True,  CSRF=True,  SameSite=Lax.
    #   Non-prod with FLASK_DEV_INSECURE=1 opt-in: Secure=False, CSRF=False, SameSite=None
    #     (legacy localhost-over-HTTP dev only — production refuses to boot in this mode,
    #      see app/__init__.py).
    _is_production: bool = get_env("FLASK_ENV", default="development") == "production"
    _dev_insecure: bool = (not _is_production) and get_env("FLASK_DEV_INSECURE", default="0") == "1"
    JWT_COOKIE_SECURE: bool = not _dev_insecure
    JWT_COOKIE_CSRF_PROTECT: bool = not _dev_insecure
    JWT_COOKIE_SAMESITE: str = "Strict" if _is_production else ("None" if _dev_insecure else "Lax")

    # Rate Limiting (flask-limiter reads RATELIMIT_STORAGE_URI from app config)
    RATELIMIT_STORAGE_URI: str = get_env("REDIS_URL", default="redis://localhost:6379/1")
    RATELIMIT_DEFAULT: str = "100 per minute"
    RATELIMIT_LOGIN: str = "5 per minute"
    # How many reverse proxies sit in front of the API and may name the caller through
    # X-Forwarded-For (app/infrastructure/trusted_proxy.py). Every limit above is keyed on
    # the caller's address, so behind a proxy with 0 hops the whole deployment shares one
    # bucket. 0 = trust nothing, keep the socket peer; production sits behind cloudflared
    # alone and sets 1.
    TRUSTED_PROXY_HOPS: int = int(get_env("TRUSTED_PROXY_HOPS", default="0"))

    # S3 / MinIO storage for invoice attachments
    S3_ENDPOINT_URL: str = get_env("S3_ENDPOINT_URL", default="http://localhost:9000")
    S3_ACCESS_KEY: str = get_env("S3_ACCESS_KEY", default="minioadmin")
    S3_SECRET_KEY: str = get_env("S3_SECRET_KEY", default="minioadmin")
    S3_BUCKET: str = get_env("S3_BUCKET", default="construction-attachments")
    S3_REGION: str = get_env("S3_REGION", default="us-east-1")
    # Public-facing S3/MinIO URL for browser-direct presigned uploads.
    # Empty = presigned upload disabled; frontend falls back to multipart POST.
    S3_PUBLIC_ENDPOINT_URL: str = get_env("S3_PUBLIC_ENDPOINT_URL", default="")

    # Swagger / OpenAPI docs surface. Default off in production to reduce
    # post-credential-leak recon; set EXPOSE_DOCS=1 to force-enable.
    EXPOSE_DOCS: bool = get_env("EXPOSE_DOCS", default="0") == "1"

    # Team chat (design 2a) ships only on deployments that opt in (AVN Construction).
    # Off → chat endpoints answer 404 and the apps hide the chat button.
    FEATURE_CHAT: bool = get_env("FEATURE_CHAT", default="0") == "1"

    # Sign in with a phone number + SMS code. "log" writes the code to the API log (dev/test);
    # "twilio" sends it through Twilio Programmable Messaging; "gateway" posts it to an
    # "SMS Gateway for Android" endpoint (sms-gate.app API, HTTP Basic auth) so it leaves from a real SIM.
    SMS_PROVIDER: str = get_env("SMS_PROVIDER", default="log")
    TWILIO_ACCOUNT_SID: str = get_env("TWILIO_ACCOUNT_SID", default="")
    TWILIO_AUTH_TOKEN: str = get_env("TWILIO_AUTH_TOKEN", default="")
    TWILIO_FROM: str = get_env("TWILIO_FROM", default="Folio")  # alphanumeric sender ID or a Twilio number
    # Full message endpoint: http://<phone-ip>:8080/message (local server) or
    # https://api.sms-gate.app/3rdparty/v1/messages (cloud relay). Credentials come from the app.
    SMS_GATEWAY_URL: str = get_env("SMS_GATEWAY_URL", default="")
    SMS_GATEWAY_USERNAME: str = get_env("SMS_GATEWAY_USERNAME", default="")
    SMS_GATEWAY_PASSWORD: str = get_env("SMS_GATEWAY_PASSWORD", default="")
    # Most SMS the whole API sends in any 10 minutes / any 24 hours (sliding windows, counted in Redis),
    # whatever the flow; past it the code routes answer 429 with a Retry-After. Size them under what the
    # SIM / gateway can really send: a team onboarding in one morning must fit. 0 switches a window off.
    SMS_GLOBAL_MAX_PER_10MIN: int = int(get_env("SMS_GLOBAL_MAX_PER_10MIN", default="60"))
    SMS_GLOBAL_MAX_PER_DAY: int = int(get_env("SMS_GLOBAL_MAX_PER_DAY", default="400"))
    # Per client address (IPv6: per /64), shared by the three routes that text an unauthenticated
    # caller (sign-up code, sign-in code, invitation code), on top of each route's "5 per minute".
    SMS_SEND_IP_LIMITS: str = get_env("SMS_SEND_IP_LIMITS", default="20 per hour;60 per day")
    OTP_TTL_SECONDS: int = int(get_env("OTP_TTL_SECONDS", default="300"))
    OTP_RESEND_SECONDS: int = int(get_env("OTP_RESEND_SECONDS", default="60"))
    OTP_HOURLY_MAX: int = int(get_env("OTP_HOURLY_MAX", default="5"))
    OTP_MAX_ATTEMPTS: int = int(get_env("OTP_MAX_ATTEMPTS", default="5"))
    # Test-only bypass code accepted in place of the real SMS code (see the single
    # comparison point in app/application/usecases/otp_login.py) so Playwright/E2E
    # can sign in without reading an SMS. Must be a 6-digit string to pass request
    # validation. Empty by default; the check itself also refuses it outside
    # development/testing, so this must stay unset in production configuration
    # (e.g. docker-compose.prod.yml).
    OTP_TEST_CODE: str = get_env("OTP_TEST_CODE", default="")
    # App Store / Play review account: ONE phone number whose sign-in code is fixed so
    # store reviewers can log in without receiving an SMS. Unlike OTP_TEST_CODE this is
    # meant for production, but it is scoped to that single number (see
    # ``_reviewer_code_for`` in app/application/usecases/otp_login.py): every other phone
    # still needs the real SMS code, and the fixed code is rejected for any other number.
    # Both values must be set for the bypass to exist; the number must be French (E.164 or
    # 0X form) and the code a 6-digit string. Leave both empty when not under review.
    OTP_REVIEWER_PHONE: str = get_env("OTP_REVIEWER_PHONE", default="")
    OTP_REVIEWER_CODE: str = get_env("OTP_REVIEWER_CODE", default="")

    # Mobile update gate (GET /api/v1/app-version). A build older than the minimum shows a
    # blocking "please update" screen. Empty = no gate. Versions are the app's x.y.z.
    APP_MIN_VERSION_IOS: str = get_env("APP_MIN_VERSION_IOS", default="")
    APP_MIN_VERSION_ANDROID: str = get_env("APP_MIN_VERSION_ANDROID", default="")
    APP_UPDATE_URL_IOS: str = get_env("APP_UPDATE_URL_IOS", default="https://testflight.apple.com/join/2Ee4zSMq")
    APP_UPDATE_URL_ANDROID: str = get_env(
        "APP_UPDATE_URL_ANDROID", default="https://play.google.com/apps/testing/com.flowitup.folio"
    )

    # Push notifications (attendance to validate / validated). "log" writes them to the API log;
    # "expo" relays through the Expo push service (APNs/FCM credentials live on the EAS project).
    PUSH_PROVIDER: str = get_env("PUSH_PROVIDER", default="log")
    EXPO_ACCESS_TOKEN: str = get_env("EXPO_ACCESS_TOKEN", default="")
    PUSH_LOCALE: str = get_env("PUSH_LOCALE", default="vi")  # vi | fr | en

    # Refresh-token lifetime for every sign-in on this deployment: "expiring" (7 days) or
    # "persistent" (never expires; the session lasts until the user signs out).
    REFRESH_TOKEN_POLICY: str = get_env("REFRESH_TOKEN_POLICY", default="expiring")

    def __post_init__(self):
        if self.JWT_TOKEN_LOCATION is None:
            self.JWT_TOKEN_LOCATION = ["headers", "cookies"]
        if self.REFRESH_TOKEN_POLICY not in ("expiring", "persistent"):
            raise ValueError("REFRESH_TOKEN_POLICY must be 'expiring' or 'persistent'")
        if self.PUSH_PROVIDER not in ("log", "expo"):
            raise ValueError("PUSH_PROVIDER must be 'log' or 'expo'")
        if self.SMS_PROVIDER not in ("log", "twilio", "gateway"):
            raise ValueError("SMS_PROVIDER must be 'log', 'twilio' or 'gateway'")
        if bool(self.OTP_REVIEWER_PHONE) != bool(self.OTP_REVIEWER_CODE):
            raise ValueError("OTP_REVIEWER_PHONE and OTP_REVIEWER_CODE must be set together")
        if self.OTP_REVIEWER_CODE and not (self.OTP_REVIEWER_CODE.isdigit() and len(self.OTP_REVIEWER_CODE) == 6):
            raise ValueError("OTP_REVIEWER_CODE must be a 6-digit string")


ENVIRONMENTS = ("development", "testing", "production")
SMS_PROVIDERS = ("log", "twilio", "gateway")


def validate_boot_settings(settings: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> None:
    """Refuse to boot on a setting that would quietly turn a safeguard off (called by ``create_app``).

    ``Config.__post_init__`` never runs (``create_app`` receives the class, not an instance), and the
    production-only guards compare FLASK_ENV with the exact word "production": a typo ("prod",
    "Production", "staging") would otherwise boot with the development defaults, and an unknown
    SMS_PROVIDER would fall back to the adapter that writes every sign-in code to the API log.
    An unset FLASK_ENV still means development.
    """
    flask_env = environ.get("FLASK_ENV")
    if flask_env is not None and flask_env not in ENVIRONMENTS:
        raise RuntimeError(f"CRITICAL: FLASK_ENV={flask_env!r} is not one of {', '.join(ENVIRONMENTS)}.")
    sms_provider = settings.get("SMS_PROVIDER", "log")
    if sms_provider not in SMS_PROVIDERS:
        raise RuntimeError(f"CRITICAL: SMS_PROVIDER={sms_provider!r} is not one of {', '.join(SMS_PROVIDERS)}.")
    if flask_env == "production" and sms_provider == "log":
        raise RuntimeError(
            "CRITICAL: SMS_PROVIDER=log would write sign-in codes to the log; not permitted in production."
        )


class DevelopmentConfig(Config):
    """Development-specific configuration."""

    DEBUG: bool = True


class ProductionConfig(Config):
    """Production-specific configuration."""

    # In production, these should be required
    DATABASE_URL: str = get_env("DATABASE_URL", required=False) or "sqlite:///dev.db"
    SECRET_KEY: str = get_env("SECRET_KEY", required=False) or "dev-secret-key"

    DEBUG: bool = False


class TestingConfig(Config):
    """Testing-specific configuration."""

    TESTING: bool = True
    DATABASE_URL: str = "sqlite:///:memory:"


# Configuration mapping
config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
