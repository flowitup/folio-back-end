"""Entry point: ``python -m app.infrastructure.browser_worker``.

Run by ``docker/browser-entrypoint.sh`` under ``xvfb-run -a`` in the ``ai-browser``
container's default mode. Wires every dependency by hand from plain env vars — see the
package docstring for why this never calls ``app.create_app()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from types import FrameType
from typing import Optional

from redis import Redis
from rq import Queue
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.adapters.s3_attachment_storage import S3AttachmentStorage
from app.infrastructure.ai.cost import RedisCostLedger
from app.infrastructure.browser_worker.agent import run_fetch, run_product_search
from app.infrastructure.browser_worker.worker import QUEUE_NAME, run_forever
from app.infrastructure.database.repositories.sqlalchemy_assistant_job_repository import (
    SqlAlchemyAssistantJobRepository,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"browser_worker: required environment variable {name} is not set")
    return value


def _assistant_enabled() -> bool:
    """The container-side ``FEATURE_ASSISTANT`` kill switch (review finding NEW-H3):
    read fresh on every call (``run_forever`` calls this every poll), the same "1" string
    convention as ``config.Config.FEATURE_ASSISTANT``."""
    return os.environ.get("FEATURE_ASSISTANT", "0") == "1"


def main() -> None:
    database_url = _require_env("DATABASE_URL")
    engine = create_engine(database_url, pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    job_repo = SqlAlchemyAssistantJobRepository(session)

    storage = S3AttachmentStorage(
        endpoint_url=_require_env("S3_ENDPOINT_URL"),
        access_key=_require_env("S3_ACCESS_KEY"),
        secret_key=_require_env("S3_SECRET_KEY"),
        bucket=_require_env("S3_BUCKET"),
        region=_env("S3_REGION", "us-east-1"),
    )
    redis_url = _require_env("REDIS_URL")
    queue = Queue(QUEUE_NAME, connection=Redis.from_url(redis_url))
    # Review finding NEW-H4: the browser agent's own DeepSeek spend was previously
    # invisible to ASSISTANT_DAILY_COST_CAP_USD entirely — built from REDIS_URL alone
    # (no Flask app/DI container in this process, see the package docstring), same
    # Redis-backed daily counter the web process's adapters bill against.
    cost_ledger = RedisCostLedger(redis_url, float(_env("ASSISTANT_DAILY_COST_CAP_USD", "5")))

    chrome_path = _env("BROWSER_CHROME_PATH", "/usr/bin/google-chrome")
    profile_dir = _env("BROWSER_PROFILE_DIR", "/app/profile")
    downloads_dir = _env("BROWSER_DOWNLOADS_DIR", "/app/data/downloads")
    deepseek_api_key = _env("DEEPSEEK_API_KEY")
    offpeak_only = _env("JOB_OFFPEAK_ONLY", "false").strip().lower() in ("1", "true")

    logger.info(
        "browser_worker: starting (offpeak_only=%s, downloads_dir=%s, profile_dir=%s)",
        offpeak_only,
        downloads_dir,
        profile_dir,
    )

    stop_event = asyncio.Event()

    def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
        logger.info("browser_worker: received signal %s, will stop after the current job", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        asyncio.run(
            run_forever(
                session=session,
                job_repo=job_repo,
                storage=storage,
                # rq.Queue.enqueue's stub is a generic method (bound to a
                # FunctionReferenceType TypeVar) that mypy cannot structurally match
                # against QueueLike's plain Protocol even though the call shape is
                # identical at runtime — narrow, deliberate escape hatch.
                queue=queue,  # type: ignore[arg-type]
                job_runner=run_fetch,
                product_search_runner=run_product_search,
                chrome_path=chrome_path,
                profile_dir=profile_dir,
                downloads_dir=downloads_dir,
                deepseek_api_key=deepseek_api_key,
                offpeak_only=offpeak_only,
                stop_event=stop_event,
                assistant_enabled=_assistant_enabled,
                cost_ledger=cost_ledger,
            )
        )
    finally:
        session.close()
    logger.info("browser_worker: stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("browser_worker: fatal error")
        sys.exit(1)
