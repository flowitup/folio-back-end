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
