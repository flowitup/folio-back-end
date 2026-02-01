"""
Application Configuration

This module reads and validates environment variables for the application.
Configuration follows the 12-factor app methodology.
"""

import os
from dataclasses import dataclass, field
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
    EMAIL_PROVIDER: str = get_env("EMAIL_PROVIDER", default="smtp")
    SMTP_HOST: str = get_env("SMTP_HOST", default="localhost")
    SMTP_PORT: int = int(get_env("SMTP_PORT", default="587"))
    SMTP_USER: str = get_env("SMTP_USER", default="")
    SMTP_PASS: str = get_env("SMTP_PASS", default="")
    SMTP_USE_TLS: bool = get_env("SMTP_USE_TLS", default="true").lower() == "true"

    # Application settings
    DEBUG: bool = get_env("FLASK_DEBUG", default="false").lower() == "true"
    TESTING: bool = False

    # JWT Configuration
    JWT_SECRET_KEY: str = get_env("JWT_SECRET_KEY", default="dev-jwt-secret-change-in-production")
    JWT_ACCESS_TOKEN_EXPIRES: timedelta = timedelta(minutes=30)
    JWT_REFRESH_TOKEN_EXPIRES: timedelta = timedelta(days=7)
    JWT_TOKEN_LOCATION: tuple = ("headers", "cookies")
    # For cross-origin requests (frontend:3000 -> backend:5000), we need SameSite=None
    # SameSite=None requires Secure=True, but for local dev over HTTP we must use False
    # In production, both should be True over HTTPS
    _is_production: bool = get_env("FLASK_ENV", default="development") == "production"
    JWT_COOKIE_SECURE: bool = _is_production
    JWT_COOKIE_CSRF_PROTECT: bool = _is_production  # Disable CSRF for dev (cross-origin)
    JWT_COOKIE_SAMESITE: str = "None" if not _is_production else "Strict"

    # Rate Limiting
    RATELIMIT_STORAGE_URL: str = get_env("REDIS_URL", default="redis://localhost:6379/1")
    RATELIMIT_DEFAULT: str = "100 per minute"
    RATELIMIT_LOGIN: str = "5 per minute"

    def __post_init__(self):
        if self.JWT_TOKEN_LOCATION is None:
            self.JWT_TOKEN_LOCATION = ["headers", "cookies"]


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
