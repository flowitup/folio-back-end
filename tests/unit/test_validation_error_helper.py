"""Unit tests for app.api._helpers.validation_error.

Covers:
- Empty loc=() — model_validator(mode='after') edge-case
- Single-element loc=('field',)
- Nested loc=('body', 'field')
- Multiple errors combined
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from app.api._helpers.validation_error import safe_validation_fields, validation_error_response


# ---------------------------------------------------------------------------
# Fixtures — tiny Pydantic models that trigger the three loc shapes
# ---------------------------------------------------------------------------


class _SingleFieldModel(BaseModel):
    name: str


class _NestedModel(BaseModel):
    class Inner(BaseModel):
        value: int

    body: Inner


class _AfterValidatorModel(BaseModel):
    x: int = 1

    @model_validator(mode="after")
    def _always_fail(self) -> "_AfterValidatorModel":
        raise ValueError("boom")


# ---------------------------------------------------------------------------
# safe_validation_fields
# ---------------------------------------------------------------------------


class TestSafeValidationFields:
    def test_empty_loc_from_model_validator(self) -> None:
        """model_validator(mode='after') produces loc=() — must not raise."""
        with pytest.raises(ValidationError) as exc_info:
            _AfterValidatorModel()
        fields = safe_validation_fields(exc_info.value)
        assert fields == ["unknown"]

    def test_single_element_loc(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _SingleFieldModel(name=123)  # type: ignore[arg-type]
        fields = safe_validation_fields(exc_info.value)
        assert fields == ["name"]

    def test_nested_loc(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _NestedModel(body={"value": "not_int"})  # type: ignore[arg-type]
        fields = safe_validation_fields(exc_info.value)
        # Nested loc is ('body', 'value') — last element should be 'value'
        assert "value" in fields

    def test_multiple_errors(self) -> None:
        """A model with two required fields should report both."""

        class _TwoFields(BaseModel):
            a: int
            b: str

        with pytest.raises(ValidationError) as exc_info:
            _TwoFields()  # type: ignore[call-arg]
        fields = safe_validation_fields(exc_info.value)
        assert set(fields) == {"a", "b"}


# ---------------------------------------------------------------------------
# validation_error_response (requires Flask app context)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _flask_app():
    """Minimal Flask app for jsonify to work."""
    from flask import Flask

    app = Flask(__name__)
    with app.app_context():
        yield app


class TestValidationErrorResponse:
    def test_returns_400_by_default(self, _flask_app) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _SingleFieldModel(name=123)  # type: ignore[arg-type]
        resp, status = validation_error_response(exc_info.value)
        assert status == 400
        data = json.loads(resp.get_data(as_text=True))
        assert data["error"] == "ValidationError"
        assert "name" in data["message"]

    def test_custom_status_code(self, _flask_app) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _SingleFieldModel(name=123)  # type: ignore[arg-type]
        resp, status = validation_error_response(exc_info.value, status_code=422)
        assert status == 422

    def test_empty_loc_does_not_crash(self, _flask_app) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _AfterValidatorModel()
        resp, status = validation_error_response(exc_info.value)
        assert status == 400
        data = json.loads(resp.get_data(as_text=True))
        assert "unknown" in data["message"]
