"""Outbound push notifications (Expo push service or a log sink)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class PushMessage:
    token: str
    title: str
    body: str
    # Small JSON payload the app uses to route the tap (kind, project_id, entry_id…).
    data: Dict[str, Any] = field(default_factory=dict)


class PushSenderPort(Protocol):
    def send(self, messages: List[PushMessage], on_invalid_token: Optional[Callable[[str], None]] = None) -> None:
        """Deliver ``messages``; report tokens the provider says are gone through ``on_invalid_token``."""
        ...
