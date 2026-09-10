"""SMS_PROVIDER is validated at start-up so a typo cannot silently fall back to the log adapter."""

from __future__ import annotations

import pytest

from config import Config


@pytest.mark.parametrize("provider", ["log", "twilio", "gateway"])
def test_known_sms_providers_are_accepted(provider):
    assert Config(SMS_PROVIDER=provider).SMS_PROVIDER == provider


def test_unknown_sms_provider_is_rejected():
    with pytest.raises(ValueError, match="SMS_PROVIDER"):
        Config(SMS_PROVIDER="sms-gateway")
