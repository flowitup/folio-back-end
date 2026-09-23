"""
Application Configuration

This module reads and validates environment variables for the application.
Configuration follows the 12-factor app methodology.
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def assistant_flags_enabled(feature_flag: object, deepseek_key: object, typesafe_key: object) -> bool:
    """Single source of truth for whether the assistant conversation is live.

    Used both by ``Config.assistant_enabled()`` (Config instance) and by
    ``app.api.v1.chat.routes.assistant_enabled()`` (reads ``current_app.config``, which
    only holds plain values copied from the class, not a live Config instance).
    """
    return bool(feature_flag) and bool(deepseek_key) and bool(typesafe_key)


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
    JWT_ACCESS_TOKEN_EXPIRES: timedelta = timedelta(minutes=30)
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

    # Folio Assistant: the pinned per-user AI conversation inside team chat. The flag
    # alone is not enough to go live — the deployment also needs the two core API keys
    # configured, so a deployment that turned the flag on before adding keys stays dark
    # instead of 500ing on first use (see assistant_enabled()).
    FEATURE_ASSISTANT: bool = get_env("FEATURE_ASSISTANT", default="0") == "1"
    DEEPSEEK_API_KEY: str = get_env("DEEPSEEK_API_KEY", default="")
    TYPESAFE_API_KEY: str = get_env("TYPESAFE_API_KEY", default="")
    GEMINI_API_KEY: str = get_env("GEMINI_API_KEY", default="")
    # How the assistant turns a scanned receipt into a clean PDF: "genai" (Gemini image
    # generation) or "opencv" (perspective-correct + threshold, no API call).
    SCAN_MODE: str = get_env("SCAN_MODE", default="genai")
    # Restrict the browser-worker's merchant-site jobs to off-peak hours (owner runbook).
    # Accepts the same spelling the ai-browser container's own parser does: "1"/"true"/
    # "yes", case-insensitive, surrounding whitespace ignored; anything else (including
    # "0"/"false"/"no"/unset) is False.
    JOB_OFFPEAK_ONLY: bool = get_env("JOB_OFFPEAK_ONLY", default="0").strip().lower() in ("1", "true", "yes")
    ASSISTANT_DAILY_COST_CAP_USD: float = float(get_env("ASSISTANT_DAILY_COST_CAP_USD", default="5"))
    # browser-use / real Chrome, used by the separate ai-browser container (phase 03).
    BROWSER_WORKER_CHROME_PATH: str = get_env("BROWSER_WORKER_CHROME_PATH", default="")
    BROWSER_WORKER_PROFILE_DIR: str = get_env("BROWSER_WORKER_PROFILE_DIR", default="")
    BROWSER_WORKER_DOWNLOADS_DIR: str = get_env("BROWSER_WORKER_DOWNLOADS_DIR", default="")

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
        if self.SCAN_MODE not in ("genai", "opencv"):
            raise ValueError("SCAN_MODE must be 'genai' or 'opencv'")

    def assistant_enabled(self) -> bool:
        """True once FEATURE_ASSISTANT is on and both core API keys are configured."""
        return assistant_flags_enabled(self.FEATURE_ASSISTANT, self.DEEPSEEK_API_KEY, self.TYPESAFE_API_KEY)


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
