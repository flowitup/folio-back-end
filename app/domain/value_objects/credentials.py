"""Credential value objects for authentication."""

import re
from dataclasses import dataclass

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


@dataclass(frozen=True, slots=True)
class Email:
    """Immutable email value object with validation."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.lower().strip()
        object.__setattr__(self, "value", normalized)
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError(f"Invalid email format: {self.value}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Password:
    """Immutable password value object with validation."""

    value: str

    def __post_init__(self) -> None:
        if len(self.value) < 8:
            raise ValueError("Password must be at least 8 characters")

    def __str__(self) -> str:
        return "********"  # Never expose password
