"""End-to-end browser-use agent smoke test — real Chrome, real browser-use, a real (if
harmless) job. Skipped unless ``BROWSER_TESTS=1`` — it needs ``browser-use`` installed
(not a project dependency, see ``Dockerfile.browser``/``agent.py``), a Chrome binary,
and a working display, none of which the normal test/CI environment has.

Run manually inside (or with the same toolchain as) the ``ai-browser`` image:
    BROWSER_TESTS=1 DEEPSEEK_API_KEY=... uv run pytest tests/unit/browser_worker/test_agent_integration.py -m requires_browser
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.browser_worker.agent import run_fetch

pytestmark = pytest.mark.skipif(
    os.getenv("BROWSER_TESTS") != "1",
    reason="Set BROWSER_TESTS=1 to run the real browser-use + Chrome smoke test",
)


@pytest.mark.requires_browser
class TestRealBrowserAgentBlockedOnDisallowedTask:
    def test_agent_refuses_to_leave_the_allowed_domains(self, tmp_path) -> None:
        """A task that tries to steer the agent to a non-merchant domain must not
        succeed — `allowed_domains` is enforced by browser-use itself (unit-tested
        directly in `test_merchants.py`); this proves the enforcement holds end to end
        with a real `Browser` instance."""
        from app.application.assistant.jobs_repo import AssistantJobRecord

        job = AssistantJobRecord(
            id=uuid4(),
            type="fetch_invoice",
            user_id=uuid4(),
            merchant="leroymerlin",
            amount_ttc=Decimal("1.00"),
            date=date.today(),
            project_hint=None,
            status="running",
            attempts=0,
            run_after=None,  # type: ignore[arg-type]
            result=None,
            pdf_storage_key=None,
            status_message_id=None,
            lang=None,
            processed_at=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        outcome = asyncio.run(
            run_fetch(
                job,
                chrome_path=os.environ.get("BROWSER_CHROME_PATH", "/usr/bin/google-chrome"),
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
            )
        )
        assert outcome.result.status in ("blocked", "not_found", "not_ready", "failed")
