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
    # "twilio" sends it through Twilio Programmable Messaging with the credentials below.
    SMS_PROVIDER: str = get_env("SMS_PROVIDER", default="log")
    TWILIO_ACCOUNT_SID: str = get_env("TWILIO_ACCOUNT_SID", default="")
    TWILIO_AUTH_TOKEN: str = get_env("TWILIO_AUTH_TOKEN", default="")
    TWILIO_FROM: str = get_env("TWILIO_FROM", default="Folio")  # alphanumeric sender ID or a Twilio number
    OTP_TTL_SECONDS: int = int(get_env("OTP_TTL_SECONDS", default="300"))
    OTP_RESEND_SECONDS: int = int(get_env("OTP_RESEND_SECONDS", default="60"))
    OTP_HOURLY_MAX: int = int(get_env("OTP_HOURLY_MAX", default="5"))
    OTP_MAX_ATTEMPTS: int = int(get_env("OTP_MAX_ATTEMPTS", default="5"))

    # Which sign-in the deployment offers: "email" (password), "phone" (SMS code) or "both".
    # The other endpoints answer 404; GET /auth/config tells the apps which screen to show.
    LOGIN_MODE: str = get_env("LOGIN_MODE", default="both")
    # Refresh-token lifetime for every sign-in on this deployment: "expiring" (7 days) or
    # "persistent" (never expires; the session lasts until the user signs out).
    REFRESH_TOKEN_POLICY: str = get_env("REFRESH_TOKEN_POLICY", default="expiring")

    def __post_init__(self):
        if self.JWT_TOKEN_LOCATION is None:
            self.JWT_TOKEN_LOCATION = ["headers", "cookies"]
        if self.LOGIN_MODE not in ("email", "phone", "both"):
            raise ValueError("LOGIN_MODE must be 'email', 'phone' or 'both'")
        if self.REFRESH_TOKEN_POLICY not in ("expiring", "persistent"):
            raise ValueError("REFRESH_TOKEN_POLICY must be 'expiring' or 'persistent'")


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
