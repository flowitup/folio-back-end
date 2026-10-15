"""Global budget of outgoing SMS: the SIM / gateway behind the sign-in codes has a finite quota."""

from __future__ import annotations

from typing import Protocol


class SmsBudgetPort(Protocol):
    def acquire(self) -> int:
        """Take one send from the budget.

        Returns 0 when the send may go ahead (it is counted), otherwise the seconds until the
        budget has room again (at least 1; nothing is counted).
        """
        ...
