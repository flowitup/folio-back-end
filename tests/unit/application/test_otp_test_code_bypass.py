"""Security tests for the non-production OTP test-code bypass.

`_test_code_accepted` (otp_login.py) is the single point where a test-only code
is allowed to substitute for a real SMS code, feeding every phone sign-in,
sign-up and invitation-acceptance flow through `_consume_code`. It must fail
closed: an unset, empty or unrecognised FLASK_ENV counts as production, not as
an accident-prone default. This is the one piece of the phone-only auth change
that makes the system weaker, so its production-refusal behaviour is tested
directly and exhaustively rather than left to incidental coverage elsewhere.
"""

from __future__ import annotations

import pytest

from app.application.usecases.otp_login import _test_code_accepted

TEST_CODE = "424242"
REAL_CODE = "111222"  # simulates the actual code a user would have received by SMS


@pytest.fixture(autouse=True)
def _isolated_otp_env(monkeypatch):
    """Every test sets FLASK_ENV/OTP_TEST_CODE explicitly; start from neither being set."""
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("OTP_TEST_CODE", raising=False)


class TestFailsClosedOutsideDevelopmentAndTesting:
    """Unset, unrecognised, or empty FLASK_ENV must all behave like production."""

    def test_production_refuses_even_with_the_var_set(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted(TEST_CODE) is False

    def test_unrecognised_value_refuses(self, monkeypatch):
        """A typo or an undeclared deployment name (e.g. 'staging') is not an allowlisted value."""
        monkeypatch.setenv("FLASK_ENV", "staging")
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted(TEST_CODE) is False

    def test_empty_string_refuses(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "")
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted(TEST_CODE) is False

    def test_unset_refuses(self, monkeypatch):
        monkeypatch.delenv("FLASK_ENV", raising=False)
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted(TEST_CODE) is False


class TestAcceptsOnlyUnderDevelopmentOrTesting:
    """The bypass activates only for the two explicit non-production environments."""

    @pytest.mark.parametrize("env_name", ["development", "testing"])
    def test_accepts_the_configured_code(self, monkeypatch, env_name):
        monkeypatch.setenv("FLASK_ENV", env_name)
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted(TEST_CODE) is True

    @pytest.mark.parametrize("env_name", ["development", "testing"])
    def test_wrong_code_is_still_refused(self, monkeypatch, env_name):
        """The bypass is one specific code, not an "anything goes" switch."""
        monkeypatch.setenv("FLASK_ENV", env_name)
        monkeypatch.setenv("OTP_TEST_CODE", TEST_CODE)
        assert _test_code_accepted("000000") is False
        assert _test_code_accepted(REAL_CODE) is False

    @pytest.mark.parametrize("env_name", ["development", "testing"])
    def test_refuses_when_otp_test_code_is_not_configured(self, monkeypatch, env_name):
        """Non-production alone is not enough: OTP_TEST_CODE must also be set and non-empty —
        this is what keeps a bare `FLASK_ENV=development` from silently accepting any code."""
        monkeypatch.setenv("FLASK_ENV", env_name)
        monkeypatch.delenv("OTP_TEST_CODE", raising=False)
        assert _test_code_accepted(TEST_CODE) is False

    @pytest.mark.parametrize("env_name", ["development", "testing"])
    def test_refuses_when_otp_test_code_is_empty(self, monkeypatch, env_name):
        monkeypatch.setenv("FLASK_ENV", env_name)
        monkeypatch.setenv("OTP_TEST_CODE", "")
        assert _test_code_accepted(TEST_CODE) is False
