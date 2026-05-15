"""Regression tests for B-1 — JWT cookie config trap.

Pre-fix behavior: any FLASK_ENV != "production" silently set
JWT_COOKIE_SECURE=False, JWT_COOKIE_CSRF_PROTECT=False, SameSite=None.
A staging/preview/demo deploy would inherit those flags and run wide-open.

Post-fix behavior:
  * Production stays Secure=True / CSRF=True / SameSite=Strict.
  * Non-prod default tightens to Secure=True / CSRF=True / SameSite=Lax.
  * FLASK_DEV_INSECURE=1 is the only opt-in for the legacy localhost-HTTP mode,
    and is rejected at boot when FLASK_ENV=production or when CORS_ORIGINS
    contains an https:// origin.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def reload_config(monkeypatch: pytest.MonkeyPatch):
    """Reload `config` with patched env so dataclass class-attrs rebind, then restore."""

    def _reload(env: dict[str, str | None]):
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        import config

        return importlib.reload(config)

    yield _reload
    # Restore default env back into the config module so subsequent tests see
    # the canonical (non-prod, no FLASK_DEV_INSECURE) values.
    monkeypatch.delenv("FLASK_DEV_INSECURE", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


class TestJwtCookieSecurityDefaults:
    def test_production_locks_cookies_down(self, reload_config):
        cfg = reload_config({"FLASK_ENV": "production", "FLASK_DEV_INSECURE": None})
        assert cfg.Config.JWT_COOKIE_SECURE is True
        assert cfg.Config.JWT_COOKIE_CSRF_PROTECT is True
        assert cfg.Config.JWT_COOKIE_SAMESITE == "Strict"

    def test_non_production_default_is_secure_lax_with_csrf(self, reload_config):
        cfg = reload_config({"FLASK_ENV": "development", "FLASK_DEV_INSECURE": None})
        assert cfg.Config.JWT_COOKIE_SECURE is True
        assert cfg.Config.JWT_COOKIE_CSRF_PROTECT is True
        assert cfg.Config.JWT_COOKIE_SAMESITE == "Lax"

    def test_dev_insecure_opt_in_relaxes_only_in_non_prod(self, reload_config):
        cfg = reload_config({"FLASK_ENV": "development", "FLASK_DEV_INSECURE": "1"})
        assert cfg.Config.JWT_COOKIE_SECURE is False
        assert cfg.Config.JWT_COOKIE_CSRF_PROTECT is False
        assert cfg.Config.JWT_COOKIE_SAMESITE == "None"

    def test_dev_insecure_is_ignored_in_production_config(self, reload_config):
        # Even if both are set, the production branch wins — Secure / CSRF / Strict.
        cfg = reload_config({"FLASK_ENV": "production", "FLASK_DEV_INSECURE": "1"})
        assert cfg.Config.JWT_COOKIE_SECURE is True
        assert cfg.Config.JWT_COOKIE_CSRF_PROTECT is True
        assert cfg.Config.JWT_COOKIE_SAMESITE == "Strict"


class TestBootGuardRejectsTrap:
    """create_app() refuses to start in unsafe env combinations.

    Boot guards are env-only reads inside create_app(), so we don't need to
    reload modules — monkeypatching os.environ is sufficient.
    """

    def test_production_with_dev_insecure_refuses_to_boot(self, monkeypatch):
        from app import create_app
        from config import TestingConfig

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("FLASK_DEV_INSECURE", "1")

        with pytest.raises(RuntimeError, match="FLASK_DEV_INSECURE=1 is not permitted"):
            create_app(TestingConfig)

    def test_dev_insecure_outside_development_refuses_to_boot(self, monkeypatch):
        """Staging/UAT must not honor the legacy insecure-cookie opt-in even
        if CORS_ORIGINS is plain http — only FLASK_ENV=development qualifies."""
        from app import create_app
        from config import TestingConfig

        monkeypatch.setenv("FLASK_ENV", "staging")
        monkeypatch.setenv("FLASK_DEV_INSECURE", "1")
        monkeypatch.setenv("CORS_ORIGINS", "https://staging.example.com")

        with pytest.raises(RuntimeError, match="only permitted when FLASK_ENV=development"):
            create_app(TestingConfig)
