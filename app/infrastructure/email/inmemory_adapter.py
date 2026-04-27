"""In-memory email adapter for tests."""

from tasks import EmailPayload


class InMemoryEmailAdapter:
    """Collects sent EmailPayload objects in memory. Use in tests only."""

    def __init__(self) -> None:
        self.sent: list[EmailPayload] = []

    def send(self, payload: EmailPayload) -> None:
        """Append payload to the sent list without any I/O."""
        self.sent.append(payload)

    def clear(self) -> None:
        """Reset the sent list between test cases."""
        self.sent.clear()
