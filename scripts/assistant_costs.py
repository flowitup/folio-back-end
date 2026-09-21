"""Owner script: print today's Folio Assistant AI spend from Redis.

Reads the same `RedisCostLedger` keys `AssistantService` checks before every pipeline
run (`assistant:cost:<date>` for the total, `assistant:cost:<date>:<kind>` per provider),
so this is a read-only view of the exact numbers the daily cost cap enforces — no
provider credentials needed, only `REDIS_URL`.

    uv run python scripts/assistant_costs.py

Exits 0 always (a fresh/idle deployment has an empty ledger, which is not an error);
prints a one-line warning when today's spend is at or above the configured cap.
"""

from __future__ import annotations

from config import Config
from app.infrastructure.ai.cost import COST_KINDS, RedisCostLedger


def main() -> int:
    ledger = RedisCostLedger(Config.REDIS_URL, Config.ASSISTANT_DAILY_COST_CAP_USD)
    by_kind = ledger.by_kind()
    total = ledger.today_total()

    print("Folio Assistant — today's AI spend (Europe/Paris)")
    print("-" * 50)
    for kind in COST_KINDS:
        print(f"  {kind:<16} ${by_kind[kind]:.5f}")
    print("-" * 50)
    print(f"  {'total':<16} ${total:.5f}  (cap ${Config.ASSISTANT_DAILY_COST_CAP_USD:.2f})")

    if ledger.over_cap():
        print(
            f"\nWARNING: today's spend has reached the ${Config.ASSISTANT_DAILY_COST_CAP_USD:.2f} cap — "
            "the pipeline is refusing every new request with the quota template."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
