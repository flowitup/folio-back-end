"""browser-use agent runner (feature B ``fetch_invoice`` + feature A ``find_product``,
owner decision D16: the browser agent also does feature A's product search, with no
external web-search or reverse-image provider involved).

``browser-use`` is deliberately NOT a project dependency (see ``Dockerfile.browser``'s
docstring / the phase report): it pins ``openai``/``pydantic``/``httpx``/``google-genai``
versions that would fight the main ``uv.lock``. It is only ever installed inside the
``ai-browser`` image, so the import here is lazy — this module (and everything that
imports it transitively, i.e. nothing outside ``app.infrastructure.browser_worker``) is
safe to import from the main codebase and the test suite without ``browser-use``
installed.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.application.assistant.models import FetchResult, MaterialIdent, ProductSearchResult
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import BROWSER_AGENT_STEP_ESTIMATE_USD
from app.infrastructure.browser_worker.merchants import (
    build_allowed_domains,
    build_product_search_task,
    build_task,
)

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

#: `agent.run()` has no deadline of its own — browser-use's per-step timeout bounds one
#: step, not the whole run, so an outer cap avoids a multi-hour worst case; kept below
#: the worker's `_STUCK_RUNNING_AFTER` reclaim window so a hung run is reported `failed`
#: here first.
AGENT_RUN_TIMEOUT_SECONDS = 20 * 60

#: Neither flow ever needs arbitrary JS execution or a file upload; `allowed_domains`
#: alone does not stop an in-page `fetch()`/XHR an `evaluate` call could issue.
EXCLUDED_TOOL_ACTIONS = ("evaluate", "upload_file")

#: Bounds for rejecting a downloaded file before trusting it as an invoice (e.g. a
#: merchant error page saved with a `.pdf` extension, or a runaway download).
MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024
_PDF_MAGIC = b"%PDF-"

#: `search_queries` come from DeepSeek's read of an untrusted photo (or a chat message
#: any channel member can post); bounded the same way other untrusted text is capped
#: before it is embedded in an agent task prompt.
MAX_SEARCH_QUERIES = 3
MAX_SEARCH_QUERY_CHARS = 80


@dataclass(frozen=True)
class FetchOutcome:
    """What one agent run produced: the structured result, plus the downloaded PDF's
    local path (on the container's ``downloads_path`` volume) when there is one."""

    result: FetchResult
    pdf_path: Optional[str]


def _is_valid_pdf(path: str) -> bool:
    """Checks the ``%PDF-`` magic bytes and a size cap — a merchant error page or a login
    wall saved with a ``.pdf`` extension would otherwise pass straight through as done."""
    try:
        size = os.path.getsize(path)
        if size == 0 or size > MAX_PDF_SIZE_BYTES:
            return False
        with open(path, "rb") as f:
            header = f.read(len(_PDF_MAGIC))
    except OSError:
        return False
    return header == _PDF_MAGIC


def _newest_pdf(downloads_dir: str, before: set[str]) -> Optional[str]:
    """The most recently modified PDF in ``downloads_dir`` that was not already there
    before this run started (plan section 4: "detect the downloaded PDF by listing
    downloads_path for new *.pdf files after the run, newest first"), discarding it when
    it fails ``_is_valid_pdf``."""
    candidates = [path for path in glob.glob(os.path.join(downloads_dir, "*.pdf")) if path not in before]
    if not candidates:
        return None
    newest = max(candidates, key=os.path.getmtime)
    if not _is_valid_pdf(newest):
        logger.warning("browser_worker: downloaded file %s failed the PDF sanity check, discarding", newest)
        return None
    return newest


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
        from browser_use import Agent, Browser, ChatOpenAI, Tools
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
        # `evaluate`/`upload_file` excluded — see EXCLUDED_TOOL_ACTIONS.
        tools = Tools(exclude_actions=list(EXCLUDED_TOOL_ACTIONS))
        agent = Agent(
            task=task, llm=llm, browser=browser, tools=tools, use_vision=True, output_model_schema=FetchResult
        )
        # Bounded by an outer deadline — see AGENT_RUN_TIMEOUT_SECONDS.
        history = await asyncio.wait_for(agent.run(max_steps=MAX_STEPS), timeout=AGENT_RUN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # SIGTERM: the poll loop (`worker.py`) cancels this call and requeues the job
        # itself — this only owns closing the browser cleanly before the cancellation
        # propagates back out (never swallowed: the requeue depends on it).
        logger.info("browser_worker: agent run cancelled (SIGTERM) for job %s, closing the browser", job.id)
        try:
            await browser.kill()
        except Exception:
            logger.exception("browser_worker: failed to close the browser after a cancelled run for job %s", job.id)
        raise
    except asyncio.TimeoutError:
        logger.error("browser_worker: agent run timed out after %ss for job %s", AGENT_RUN_TIMEOUT_SECONDS, job.id)
        try:
            await browser.kill()
        except Exception:
            logger.exception("browser_worker: failed to close the browser after a timed-out run for job %s", job.id)
        return FetchOutcome(result=FetchResult(status="failed", message="Agent run timed out."), pdf_path=None)
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


def _clamp_search_queries(queries: list[Any]) -> list[str]:
    """Bounds untrusted `search_queries` before they reach the agent's task text: at most
    `MAX_SEARCH_QUERIES` items, each collapsed to a single line and truncated to
    `MAX_SEARCH_QUERY_CHARS` characters. Duplicates (after truncation) are dropped."""
    clamped: list[str] = []
    for raw in queries:
        text = " ".join(str(raw).split())[:MAX_SEARCH_QUERY_CHARS]
        if text and text not in clamped:
            clamped.append(text)
        if len(clamped) >= MAX_SEARCH_QUERIES:
            break
    return clamped


async def run_product_search(
    job: AssistantJobRecord,
    *,
    chrome_path: str,
    profile_dir: str,
    downloads_dir: str,
    deepseek_api_key: str,
    cost_ledger: Optional[CostLedgerPort] = None,
) -> ProductSearchResult:
    """Runs one browser-use agent session for a ``find_product`` job (feature A, owner
    decision D16): searches the allow-listed merchant sites' own search pages for the
    material identified in ``job.params["ident"]`` instead of calling a web-search API.

    Mirrors ``run_fetch`` above (never raises, same ``Browser``/allowed-domains/cost
    billing) minus the PDF-download bookkeeping — a product search has no file to save,
    only the structured ``ProductSearchResult`` the agent's final answer carries.
    """
    try:
        from browser_use import Agent, Browser, ChatOpenAI, Tools
    except Exception as exc:  # pragma: no cover - only unreachable inside the real image
        logger.exception("browser_worker: browser-use is not importable")
        return ProductSearchResult(status="failed", message=f"browser-use unavailable: {exc}")

    params = job.params or {}
    ident = MaterialIdent.model_validate(params.get("ident") or {})
    raw_queries = params.get("search_queries") or ident.search_queries or [ident.name]
    queries = _clamp_search_queries(raw_queries) or _clamp_search_queries([ident.name])
    task = build_product_search_task(ident, queries)

    # This flow reads untrusted photo-derived text and visits third-party marketplace
    # listings (ManoMano sellers) — it must never run inside the merchant-login profile
    # (`profile_dir`), so a prompt-injected page can never touch a logged-in session. A
    # fresh, ephemeral, never-logged-in profile per run, removed afterward regardless of
    # outcome.
    search_profile_dir = tempfile.mkdtemp(prefix="folio-product-search-")
    try:
        browser = Browser(
            executable_path=chrome_path,
            user_data_dir=search_profile_dir,
            headless=False,
            downloads_path=downloads_dir,
            allowed_domains=build_allowed_domains(),
            keep_alive=False,
            # See run_fetch's identical comment: the container is the isolation boundary.
            chromium_sandbox=False,
        )
        llm = ChatOpenAI(model=_LLM_MODEL, base_url=_LLM_BASE_URL, api_key=deepseek_api_key)
        tools = Tools(exclude_actions=list(EXCLUDED_TOOL_ACTIONS))
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            tools=tools,
            use_vision=True,
            output_model_schema=ProductSearchResult,
        )
        history = await asyncio.wait_for(agent.run(max_steps=MAX_STEPS), timeout=AGENT_RUN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # See `run_fetch`'s identical handler.
        logger.info(
            "browser_worker: product search agent run cancelled (SIGTERM) for job %s, closing the browser", job.id
        )
        try:
            await browser.kill()
        except Exception:
            logger.exception("browser_worker: failed to close the browser after a cancelled run for job %s", job.id)
        raise
    except asyncio.TimeoutError:
        logger.error(
            "browser_worker: product search agent run timed out after %ss for job %s",
            AGENT_RUN_TIMEOUT_SECONDS,
            job.id,
        )
        try:
            await browser.kill()
        except Exception:
            logger.exception("browser_worker: failed to close the browser after a timed-out run for job %s", job.id)
        return ProductSearchResult(status="failed", message="Agent run timed out.")
    except Exception as exc:
        logger.exception("browser_worker: product search agent run failed for job %s", job.id)
        return ProductSearchResult(status="failed", message=str(exc))
    finally:
        shutil.rmtree(search_profile_dir, ignore_errors=True)

    if cost_ledger is not None:
        try:
            cost_ledger.add(COST_KIND, _browser_agent_cost_usd(history))
        except Exception:
            # See run_fetch's identical comment: under-billing one run is far cheaper
            # than losing a search that otherwise succeeded.
            logger.exception("browser_worker: failed to bill the cost ledger for job %s", job.id)

    return _extract_product_search_result(history)


def _extract_product_search_result(history: object) -> ProductSearchResult:
    try:
        raw = history.final_result()  # type: ignore[attr-defined]
    except Exception:
        raw = None
    if raw:
        try:
            return ProductSearchResult.model_validate_json(raw)
        except Exception:
            logger.warning("browser_worker: agent final_result failed ProductSearchResult validation: %r", raw)
    return ProductSearchResult(status="failed", message="Agent produced no usable result.")


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


__all__ = ["FetchOutcome", "run_fetch", "run_product_search", "MAX_STEPS"]
