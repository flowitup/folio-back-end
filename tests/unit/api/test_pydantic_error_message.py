"""Client-facing text for a request that fails schema validation."""


def test_validation_message_names_fields_without_input_or_docs_link():
    from pydantic import BaseModel, Field, ValidationError

    from app.api._helpers.pydantic_errors import validation_message

    class Body(BaseModel):
        title: str = Field(min_length=1)

    try:
        Body.model_validate({"title": ""})
    except ValidationError as exc:
        message = validation_message(exc)
    assert message.startswith("title: String should have at least 1 character")
    assert "pydantic.dev" not in message
    assert "input_value" not in message
    assert "Body" not in message


def test_model_level_error_keeps_its_reason_without_a_value_prefix():
    from pydantic import BaseModel, ValidationError, model_validator

    from app.api._helpers.pydantic_errors import validation_message

    class Range(BaseModel):
        start: int
        end: int

        @model_validator(mode="after")
        def _ordered(self):
            if self.start > self.end:
                raise ValueError("from must be <= to")
            return self

    try:
        Range.model_validate({"start": 2, "end": 1})
    except ValidationError as exc:
        message = validation_message(exc)
    assert message == "from must be <= to"


def test_format_validation_error_message_matches_validation_message():
    from flask import Flask
    from pydantic import BaseModel, ValidationError, field_validator

    from app.api._helpers.pydantic_errors import format_validation_error

    class Query(BaseModel):
        month: str

        @field_validator("month")
        @classmethod
        def _month(cls, v):
            raise ValueError("must be YYYY-MM")

    try:
        Query.model_validate({"month": "x"})
    except ValidationError as exc:
        with Flask(__name__).app_context():
            resp, status = format_validation_error(exc)
            body = resp.get_json()
    assert status == 422
    assert body["message"] == "month: must be YYYY-MM"
    assert body["details"][0]["loc"] == ["month"]
