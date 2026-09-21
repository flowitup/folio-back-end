"""Owner smoke test for the real DeepSeek + Jev credentials — one router call, one extraction.

Not run in CI (needs real DEEPSEEK_API_KEY/TYPESAFE_API_KEY and makes real network calls,
which would cost money and cannot be part of a deterministic test suite). Run by hand once
the owner has added the keys to `.env`:

    uv run python scripts/ai_smoke.py [path/to/a/receipt-or-invoice.jpg]

Prints the router decision for a hard-coded chat message, and — if an image path is given
— the S1 extraction result for that image. Exits non-zero with a clear message when a key
is missing, rather than a stack trace.
"""

from __future__ import annotations

import sys

from config import Config


def main() -> int:
    if not Config.DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY is not set — add it to .env first.", file=sys.stderr)
        return 1
    if not Config.TYPESAFE_API_KEY:
        print("TYPESAFE_API_KEY is not set — add it to .env first.", file=sys.stderr)
        return 1

    from app.application.assistant.router import Router
    from app.infrastructure.ai.cost import InMemoryCostLedger
    from app.infrastructure.ai.deepseek_client import DeepSeekVisionLlm
    from app.infrastructure.ai.jev_client import JevDecisionPort

    cost_ledger = InMemoryCostLedger()
    decisions = JevDecisionPort(Config.TYPESAFE_API_KEY, cost_ledger)
    router = Router(decisions)
    message = "máy cắt gạch ở đâu?"
    decision = router.route(message, has_photo=False, project_names=["Villa Arcueil"], history_texts=[])
    print(f"Router decision for {message!r}:")
    print(f"  intent={decision.intent} (confidence={decision.intent_confidence:.2f})")
    print(f"  merchant={decision.merchant} project_hint={decision.project_hint} is_write={decision.is_write:.2f}")

    if len(sys.argv) > 1:
        from app.application.assistant.extract import extract_invoice

        vision = DeepSeekVisionLlm(Config.DEEPSEEK_API_KEY, cost_ledger)
        with open(sys.argv[1], "rb") as handle:
            image_bytes = handle.read()
        invoice = extract_invoice(vision, [image_bytes])
        print(f"\nS1 extraction for {sys.argv[1]}:")
        print(invoice.model_dump_json(indent=2))
        print(f"\nDeepSeek cost so far today (this process): ${cost_ledger.today_total():.5f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
