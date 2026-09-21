"""Unit tests for ``app.application.assistant.scope`` (D17 layer 1: redaction)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.assistant.models import ChannelScope
from app.application.assistant.scope import (
    CLASS_FIELDS,
    CONFIDENTIAL_CLASSES,
    allowed_classes_for,
    redact,
)


def _scope(*, is_admin_channel: bool) -> ChannelScope:
    return ChannelScope(
        kind="admin" if is_admin_channel else "company",
        company_id=uuid4(),
        project_id=None,
        is_admin_channel=is_admin_channel,
        asker_id=uuid4(),
    )


class TestAllowedClasses:
    def test_admin_channel_allows_every_confidential_class(self) -> None:
        assert allowed_classes_for(True) == CONFIDENTIAL_CLASSES

    def test_non_admin_channel_allows_none(self) -> None:
        assert allowed_classes_for(False) == frozenset()

    def test_channel_scope_computes_allowed_classes_from_is_admin_channel(self) -> None:
        assert _scope(is_admin_channel=True).allowed_classes == CONFIDENTIAL_CLASSES
        assert _scope(is_admin_channel=False).allowed_classes == frozenset()


class TestRedactNonAdminScope:
    """Field-by-field: every finance_company/payroll field is stripped."""

    scope = _scope(is_admin_channel=False)

    @pytest.mark.parametrize("field_name", sorted(CLASS_FIELDS["finance_company"]))
    def test_strips_every_finance_company_field(self, field_name: str) -> None:
        payload = {field_name: 123.45, "safe": "ok"}
        assert redact(self.scope, payload) == {"safe": "ok"}

    @pytest.mark.parametrize("field_name", sorted(CLASS_FIELDS["payroll"]))
    def test_strips_every_payroll_field(self, field_name: str) -> None:
        payload = {field_name: 42, "safe": "ok"}
        assert redact(self.scope, payload) == {"safe": "ok"}

    @pytest.mark.parametrize(
        "key", ["worker_daily_rate", "monthly_salary", "project_budget", "funds_released", "income_remaining"]
    )
    def test_strips_any_key_matching_a_catch_all_suffix(self, key: str) -> None:
        payload = {key: 999}
        assert redact(self.scope, payload) == {}

    def test_recurses_into_nested_dicts_and_lists(self) -> None:
        payload = {
            "card": {
                "title": "Leroy Merlin",
                "extra": {"total_ttc": 79.5, "budget": 1000},
            },
            "options": [{"label": "ok", "payload": {"daily_rate": 50}}],
        }
        assert redact(self.scope, payload) == {
            "card": {"title": "Leroy Merlin", "extra": {}},
            "options": [{"label": "ok", "payload": {}}],
        }

    def test_none_payload_passes_through(self) -> None:
        assert redact(self.scope, None) is None

    def test_none_scope_never_redacts(self) -> None:
        payload = {"budget": 1000}
        assert redact(None, payload) == payload


class TestRedactAdminScope:
    scope = _scope(is_admin_channel=True)

    def test_admin_scope_is_a_no_op(self) -> None:
        payload = {"budget": 1000, "daily_rate": 50, "released_funds": 200}
        assert redact(self.scope, payload) == payload
