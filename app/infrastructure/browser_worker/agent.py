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
from typing import Optional

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import FetchResult
from app.infrastructure.browser_worker.merchants import build_allowed_domains, build_task

logger = logging.getLogger(__name__)

#: plan section 4: `Agent(..., max_steps=40)`.
MAX_STEPS = 40
#: `deepseek-flash` doubles as the browser agent's driving LLM (plan section 0's model
#: role table: "Hands: browser-use + real Google Chrome" driven by the same DeepSeek).
_LLM_MODEL = "deepseek-flash"
_LLM_BASE_URL = "https://api.deepseek.com"


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
) -> FetchOutcome:
    """Runs one browser-use agent session for ``job``. Never raises: every failure mode
    (missing dependency, agent exception, unparseable structured output) is caught and
    reported as a ``FetchResult(status="failed", ...)`` outcome — the caller (the poll
    loop) always has something to write back to ``assistant_jobs``."""
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

    pdf_path = _newest_pdf(downloads_dir, before)
    result = _extract_result(history, has_pdf=pdf_path is not None)
    return FetchOutcome(result=result, pdf_path=pdf_path)


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
