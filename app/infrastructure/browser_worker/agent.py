"""browser-use agent runner (feature B).

``browser-use`` is deliberately NOT a project dependency (see ``Dockerfile.browser``'s
docstring / the phase report): it pins ``openai``/``pydantic``/``httpx``/``google-genai``
versions that would fight the main ``uv.lock``. It is only ever installed inside the
``ai-browser`` image, so the import here is lazy — this module (and everything that
imports it transitively, i.e. nothing outside ``app.infrastructure.browser_worker``) is
safe to import from the main codebase and the test suite without ``browser-use``
installed.
"""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import FetchResult
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import BROWSER_AGENT_STEP_ESTIMATE_USD
from app.infrastructure.browser_worker.merchants import build_allowed_domains, build_task

logger = logging.getLogger(__name__)

#: plan section 4: `Agent(..., max_steps=40)`.
MAX_STEPS = 40
#: `deepseek-flash` doubles as the browser agent's driving LLM (plan section 0's model
#: role table: "Hands: browser-use + real Google Chrome" driven by the same DeepSeek).
_LLM_MODEL = "deepseek-flash"
_LLM_BASE_URL = "https://api.deepseek.com"
#: The kind this container's own DeepSeek spend is billed under (review finding NEW-H4)
#: — see `app.infrastructure.ai.cost.COST_KINDS`.
COST_KIND = "deepseek_browser"


@dataclass(frozen=True)
class FetchOutcome:
    """What one agent run produced: the structured result, plus the downloaded PDF's
    local path (on the container's ``downloads_path`` volume) when there is one."""

    result: FetchResult
    pdf_path: Optional[str]


def _newest_pdf(downloads_dir: str, before: set[str]) -> Optional[str]:
    """The most recently modified PDF in ``downloads_dir`` that was not already there
    before this run started (plan section 4: "detect the downloaded PDF by listing
    downloads_path for new *.pdf files after the run, newest first")."""
    candidates = [path for path in glob.glob(os.path.join(downloads_dir, "*.pdf")) if path not in before]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


async def run_fetch(
    job: AssistantJobRecord,
    *,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    cost_ledger: Optional[CostLedgerPort] = None,
) -> FetchOutcome:
    """Runs one browser-use agent session for ``job``. Never raises: every failure mode
    (missing dependency, agent exception, unparseable structured output) is caught and
    reported as a ``FetchResult(status="failed", ...)`` outcome — the caller (the poll
    loop) always has something to write back to ``assistant_jobs``.

    ``cost_ledger`` (review finding NEW-H4): before this fix, the single most
    token-hungry call in the whole pipeline (a vision-enabled ``browser-use`` agent loop,
    up to ``MAX_STEPS`` turns) ran in a container with no ``CostLedgerPort`` at all, so
    ``ASSISTANT_DAILY_COST_CAP_USD`` under-counted the true daily spend by construction.
    ``None`` (the default, kept for the standalone smoke test in
    ``test_agent_integration.py``) skips billing entirely rather than failing the job —
    a missing ledger must never block a fetch that otherwise succeeded.
    """
    before = set(glob.glob(os.path.join(downloads_dir, "*.pdf")))
    try:
        from browser_use import Agent, Browser, ChatOpenAI
    except Exception as exc:  # pragma: no cover - only unreachable inside the real image
        logger.exception("browser_worker: browser-use is not importable")
        return FetchOutcome(
            result=FetchResult(status="failed", message=f"browser-use unavailable: {exc}"), pdf_path=None
        )

    task = build_task(job)
    try:
        browser = Browser(
            executable_path=chrome_path,
            user_data_dir=profile_dir,
            headless=False,
            downloads_path=downloads_dir,
            allowed_domains=build_allowed_domains(),
            keep_alive=False,
            # Chrome's own sandbox tries to create a user/PID namespace (unshare(2)),
            # which a plain Docker container's default seccomp/capability set refuses
            # ("Operation not permitted") — confirmed by hand against the built image.
            # The container itself is the isolation boundary here (non-root user,
            # allowed_domains restricted to the merchant list); the CI/prod host never
            # grants extra namespace capabilities to this image, so the sandbox must be
            # disabled the same way every "Chrome in Docker" deployment does.
            chromium_sandbox=False,
        )
        llm = ChatOpenAI(model=_LLM_MODEL, base_url=_LLM_BASE_URL, api_key=deepseek_api_key)
        agent = Agent(task=task, llm=llm, browser=browser, use_vision=True, output_model_schema=FetchResult)
        history = await agent.run(max_steps=MAX_STEPS)
    except Exception as exc:
        logger.exception("browser_worker: agent run failed for job %s", job.id)
        return FetchOutcome(result=FetchResult(status="failed", message=str(exc)), pdf_path=None)

    if cost_ledger is not None:
        try:
            cost_ledger.add(COST_KIND, _browser_agent_cost_usd(history))
        except Exception:
            # A ledger write failing (Redis blip) must never turn an otherwise-successful
            # fetch into a failed job — under-billing one run is far cheaper than losing
            # the invoice the user is waiting for.
            logger.exception("browser_worker: failed to bill the cost ledger for job %s", job.id)

    pdf_path = _newest_pdf(downloads_dir, before)
    result = _extract_result(history, has_pdf=pdf_path is not None)
    return FetchOutcome(result=result, pdf_path=pdf_path)


def _browser_agent_cost_usd(history: Any) -> float:
    """USD spent by one ``agent.run()`` call (review finding NEW-H4).

    Prefers browser-use's own accounting (``history.usage.total_cost`` — real token
    counts priced against litellm's live pricing table, populated by
    ``token_cost_service.get_usage_summary()`` once ``run()`` returns). Falls back to a
    flat per-step estimate when that comes back empty: an unrecognized model name in
    litellm's pricing table (``deepseek-flash`` may not be listed there) or the
    pricing-data fetch itself failing both silently yield ``total_cost == 0.0`` rather
    than raising, so a zero real cost and a zero unknown-pricing cost are
    indistinguishable from here — treating a reported zero as "unavailable" is the safer
    of the two ways to be wrong (it never under-bills a real run to $0).
    """
    usage = getattr(history, "usage", None)
    total_cost = getattr(usage, "total_cost", None) if usage is not None else None
    if total_cost:
        return float(total_cost)
    steps = len(history) if hasattr(history, "__len__") else 0
    return steps * BROWSER_AGENT_STEP_ESTIMATE_USD


def _extract_result(history: object, *, has_pdf: bool) -> FetchResult:
    try:
        raw = history.final_result()  # type: ignore[attr-defined]
    except Exception:
        raw = None
    if raw:
        try:
            return FetchResult.model_validate_json(raw)
        except Exception:
            logger.warning("browser_worker: agent final_result failed FetchResult validation: %r", raw)
    # No usable structured output — fall back on whether a PDF actually landed.
    if has_pdf:
        return FetchResult(status="done", message="PDF downloaded; agent produced no structured result.")
    return FetchResult(status="failed", message="Agent produced no usable result and no PDF was downloaded.")


__all__ = ["FetchOutcome", "run_fetch", "MAX_STEPS"]
