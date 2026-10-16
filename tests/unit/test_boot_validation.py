"""create_app refuses settings that would quietly turn a safeguard off.

create_app receives the Config class (not an instance), so every check lives in
validate_boot_settings. The production-only guards compare FLASK_ENV with the exact word "production": a typo must stop the boot, not fall back to development
defaults, and an unknown SMS_PROVIDER must not fall back to the adapter that logs every sign-in code.
"""

from __future__ import annotations

import pytest

import wiring
from app import create_app
from config import Config, TestingConfig, validate_boot_settings


@pytest.mark.parametrize("flask_env", ["prod", "Production", "PRODUCTION", "staging", "dev", " production", ""])
def test_unknown_flask_env_is_refused(flask_env):
    with pytest.raises(RuntimeError, match="FLASK_ENV"):
        validate_boot_settings({"SMS_PROVIDER": "gateway"}, {"FLASK_ENV": flask_env})


@pytest.mark.parametrize("environ", [{}, {"FLASK_ENV": "development"}, {"FLASK_ENV": "testing"}])
def test_unset_development_and_testing_still_boot(environ):
    validate_boot_settings({"SMS_PROVIDER": "log"}, environ)
    validate_boot_settings({}, environ)  # SMS_PROVIDER absent: wiring falls back to "log"


@pytest.mark.parametrize("provider", ["sms-gateway", "Gateway", "", "none"])
@pytest.mark.parametrize("environ", [{}, {"FLASK_ENV": "development"}, {"FLASK_ENV": "production"}])
def test_unknown_sms_provider_is_refused_everywhere(provider, environ):
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        validate_boot_settings({"SMS_PROVIDER": provider}, environ)


@pytest.mark.parametrize("provider", ["twilio", "gateway"])
def test_production_accepts_real_providers(provider):
    validate_boot_settings({"SMS_PROVIDER": provider}, {"FLASK_ENV": "production"})


@pytest.mark.parametrize("settings", [{"SMS_PROVIDER": "log"}, {}])
def test_production_refuses_the_log_provider_even_when_it_is_the_default(settings):
    with pytest.raises(RuntimeError, match="SMS_PROVIDER=log"):
        validate_boot_settings(settings, {"FLASK_ENV": "production"})


@pytest.mark.parametrize("limits", ["twenty per hour", "20 per fortnight", "0 per hour", "20 per hour;lots per day"])
def test_a_malformed_sms_send_ip_limit_stops_the_boot(limits):
    with pytest.raises(RuntimeError, match="SMS_SEND_IP_LIMITS"):
        validate_boot_settings({"SMS_PROVIDER": "gateway", "SMS_SEND_IP_LIMITS": limits}, {})


@pytest.mark.parametrize(
    "name", ["SMS_GLOBAL_MAX_PER_10MIN", "SMS_GLOBAL_MAX_PER_DAY", "SMS_OPEN_MAX_PER_10MIN", "SMS_OPEN_MAX_PER_DAY"]
)
@pytest.mark.parametrize("value", ["lots", "-1", None, "1.5"])
def test_a_malformed_sms_budget_stops_the_boot(name, value):
    with pytest.raises(RuntimeError, match=name):
        validate_boot_settings({"SMS_PROVIDER": "gateway", name: value}, {})


def test_valid_sms_limits_and_a_switched_off_budget_boot():
    validate_boot_settings(
        {
            "SMS_PROVIDER": "gateway",
            "SMS_SEND_IP_LIMITS": "20 per hour;60 per day",
            "SMS_GLOBAL_MAX_PER_10MIN": 0,
            "SMS_GLOBAL_MAX_PER_DAY": "400",
            "SMS_OPEN_MAX_PER_10MIN": 0,
            "SMS_OPEN_MAX_PER_DAY": "150",
        },
        {},
    )
    validate_boot_settings({"SMS_PROVIDER": "gateway", "SMS_SEND_IP_LIMITS": ""}, {})


def test_empty_sms_limit_variables_fall_back_to_the_defaults():
    """Read in a fresh interpreter: the values are class attributes fixed at import."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {
        **os.environ,
        "SMS_GLOBAL_MAX_PER_10MIN": " ",
        "SMS_GLOBAL_MAX_PER_DAY": "",
        "SMS_OPEN_MAX_PER_10MIN": " ",
        "SMS_OPEN_MAX_PER_DAY": "",
        "SMS_SEND_IP_LIMITS": " ",
    }
    code = (
        "from config import Config as C; print(C.SMS_GLOBAL_MAX_PER_10MIN, C.SMS_GLOBAL_MAX_PER_DAY, "
        "C.SMS_OPEN_MAX_PER_10MIN, C.SMS_OPEN_MAX_PER_DAY, C.SMS_SEND_IP_LIMITS)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True, cwd=Path(__file__).parents[2]
    )
    assert out.stdout.strip() == "60 400 30 150 20 per hour;60 per day"


def test_create_app_refuses_a_mistyped_environment(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "prod")
    with pytest.raises(RuntimeError, match="FLASK_ENV"):
        create_app(TestingConfig)


def test_create_app_refuses_an_unknown_sms_provider(monkeypatch):
    class TypoConfig(TestingConfig):
        SMS_PROVIDER = "sms-gateway"

    monkeypatch.delenv("FLASK_ENV", raising=False)
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        create_app(TypoConfig)


def test_create_app_refuses_the_log_provider_in_production(monkeypatch):
    class ProdLogConfig(TestingConfig):
        SECRET_KEY = "test-secret-flask-key-safe-for-prod-guard"
        JWT_SECRET_KEY = "test-secret-jwt-key-safe-for-prod-guard"
        S3_ACCESS_KEY = "test-access-key"
        S3_SECRET_KEY = "test-secret-key"
        S3_ENDPOINT_URL = "http://s3.test-bucket.example.com"
        SMS_PROVIDER = "log"

    monkeypatch.setenv("FLASK_ENV", "production")
    with pytest.raises(RuntimeError, match="SMS_PROVIDER=log"):
        create_app(ProdLogConfig)


def test_the_production_deployment_values_still_boot(monkeypatch):
    """docker-compose.prod.yml: FLASK_ENV=production, SMS_PROVIDER=gateway, TRUSTED_PROXY_HOPS=1 (fake secrets)."""
    for key, value in (
        ("FLASK_ENV", "production"),
        ("SMS_PROVIDER", "gateway"),
        ("TRUSTED_PROXY_HOPS", "1"),
        ("FEATURE_CHAT", "1"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FLASK_DEV_INSECURE", raising=False)

    class ProdDeployConfig(Config):
        TESTING = False
        DEBUG = False
        DATABASE_URL = "sqlite:///:memory:"
        SECRET_KEY = "test-secret-flask-key-safe-for-prod-guard"
        JWT_SECRET_KEY = "test-secret-jwt-key-safe-for-prod-guard"
        S3_ACCESS_KEY = "test-access-key"
        S3_SECRET_KEY = "test-secret-key"
        S3_ENDPOINT_URL = "http://s3.test-bucket.example.com"
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        SMS_PROVIDER = "gateway"
        SMS_GATEWAY_URL = "https://sms-gateway.example.com/message"
        SMS_GATEWAY_USERNAME = "test-user"
        SMS_GATEWAY_PASSWORD = "test-password"
        TRUSTED_PROXY_HOPS = 1

    saved_container = wiring.container
    try:
        app = create_app(ProdDeployConfig)
        sender = wiring.get_container().sms_sender
        assert type(sender).__name__ == "BudgetedSmsSender"
        assert type(sender._sender).__name__ == "SmsGatewaySender"
        assert app.config["TRUSTED_PROXY_HOPS"] == 1
    finally:
        wiring.container = saved_container


# --- config-deploy-02 / -10, recent-backend-14: the checks Config.__post_init__ never ran -------


@pytest.mark.parametrize("value", ["Expo", "EXPO", " expo "])
def test_push_provider_is_case_insensitive(value):
    validate_boot_settings({"SMS_PROVIDER": "log", "PUSH_PROVIDER": value}, {})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PUSH_PROVIDER", "expo-push"),
        ("EMAIL_PROVIDER", "sendgrid"),
        ("REFRESH_TOKEN_POLICY", "forever"),
        ("APP_MIN_VERSION_IOS", "v1.2.0"),
        ("APP_MIN_VERSION_ANDROID", "1.2"),
    ],
)
def test_a_typo_in_a_setting_stops_the_boot(key, value):
    with pytest.raises(RuntimeError, match=key):
        validate_boot_settings({"SMS_PROVIDER": "log", key: value}, {})


@pytest.mark.parametrize(
    "reviewer",
    [
        {"OTP_REVIEWER_PHONE": "+33600000099"},
        {"OTP_REVIEWER_CODE": "123456"},
        {"OTP_REVIEWER_PHONE": "+33600000099", "OTP_REVIEWER_CODE": "12345"},
        {"OTP_REVIEWER_PHONE": "+33600000099", "OTP_REVIEWER_CODE": "12a456"},
    ],
)
def test_a_half_set_or_malformed_reviewer_login_stops_the_boot(reviewer):
    with pytest.raises(RuntimeError, match="OTP_REVIEWER"):
        validate_boot_settings({"SMS_PROVIDER": "log", **reviewer}, {})


def test_valid_optional_settings_boot():
    validate_boot_settings(
        {
            "SMS_PROVIDER": "gateway",
            "PUSH_PROVIDER": "expo",
            "EMAIL_PROVIDER": "resend",
            "REFRESH_TOKEN_POLICY": "persistent",
            "OTP_REVIEWER_PHONE": "+33600000099",
            "OTP_REVIEWER_CODE": "123456",
            "APP_MIN_VERSION_IOS": "1.4.0",
            "APP_UPDATE_URL_IOS": "https://apps.apple.com/app/id0000000000",
            "APP_MIN_VERSION_ANDROID": "",
        },
        {"FLASK_ENV": "production"},
    )


@pytest.mark.parametrize("provider", ["smtp", "log"])
def test_the_log_email_provider_writes_the_mail_to_the_log(provider, monkeypatch, caplog):
    from tasks import EmailPayload

    monkeypatch.setenv("EMAIL_PROVIDER", provider)
    adapter = wiring._build_email_port()
    assert adapter is not None
    with caplog.at_level("WARNING"):
        adapter.send(EmailPayload(to="a@example.com", subject="Invitation", body="http://x/accept-invite/t"))
    assert "accept-invite" in caplog.text


def test_an_empty_variable_counts_as_unset(monkeypatch):
    from config import get_env

    monkeypatch.setenv("FOLIO_TEST_EMPTY", "")
    assert get_env("FOLIO_TEST_EMPTY", default="300") == "300"


def test_a_minimum_version_needs_its_store_link():
    with pytest.raises(RuntimeError, match="APP_UPDATE_URL_ANDROID"):
        validate_boot_settings({"SMS_PROVIDER": "log", "APP_MIN_VERSION_ANDROID": "1.2.0"}, {})
