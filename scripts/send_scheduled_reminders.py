"""Send the scheduled reminder pushes that are due now (owner decision QĐ-9).

- managers: "no attendance logged today", 19:30 to 22:00 Paris on working days
- company admins: overdue factures, Monday 09:00 to 18:00 Paris
- company admins: summary of the month just ended, the 1st 09:00 to 18:00 Paris

Run every 15 minutes by the deploy repo (scripts/backup/install-backup-cron.sh in
flowitup/folio), inside the API container:

    docker compose exec -T -e PYTHONPATH=/app api python scripts/send_scheduled_reminders.py

The host cron runs on UTC; what is due is decided on the Europe/Paris wall clock, so
daylight saving needs nothing. Every send is recorded (scheduled_reminder_log), so a re-run
sends nothing twice and a person gets at most one reminder push a day.

Usage (from folio-back-end/):

    uv run python scripts/send_scheduled_reminders.py --dry-run
    uv run python scripts/send_scheduled_reminders.py --dry-run --now 2026-11-23T19:35+01:00

``--now`` takes an ISO datetime (no offset = UTC). ``--dry-run`` prints who would get what
and writes nothing (no push, no bell entry, no log row).

Exit codes: 0 success, 1 bad arguments or wiring.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable
from uuid import UUID

from app import create_app, db
from app.application.push.dispatcher import PushDispatcher
from app.application.push.scheduled_reminders import SendScheduledRemindersUseCase
from app.infrastructure.adapters.sqlalchemy_scheduled_reminders import SQLAlchemyReminderLog, SQLAlchemyReminderSource


def get_container() -> Any:
    """The DI container from wiring.py at the repo root.

    `python scripts/<name>.py` puts scripts/ first on sys.path, not the repo root (the editable
    install maps only app/ and config/), so `import wiring` fails when cron or
    `docker compose exec` starts the script that way. Add the root before importing.
    """
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from wiring import get_container as wiring_container

    return wiring_container()


def _parse_now(raw: str | None) -> datetime | None:
    if not raw:
        return None
    moment = datetime.fromisoformat(raw)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _people(session: Any, user_ids: Iterable[UUID]) -> Dict[UUID, str]:
    from app.infrastructure.database.models.user import UserModel

    ids = list(user_ids)
    if not ids:
        return {}
    rows = session.query(UserModel.id, UserModel.display_name, UserModel.email).filter(UserModel.id.in_(ids))
    return {r[0]: (r[1] or r[2] or str(r[0])) for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the scheduled reminder pushes that are due now.")
    parser.add_argument("--dry-run", action="store_true", help="print who would get what; write and send nothing")
    parser.add_argument("--now", help="pretend the time is this ISO datetime (no offset = UTC)")
    args = parser.parse_args()
    try:
        at = _parse_now(args.now)
    except ValueError:
        print(f"ERROR: --now is not an ISO datetime: {args.now}", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("reminders")

    app = create_app()
    with app.app_context():
        c = get_container()
        if c.push_device_repository is None or c.push_sender is None:
            print("ERROR: push is not wired", file=sys.stderr)
            return 1
        session = db.session
        # Synchronous delivery: the process exits right after the run, which would kill the
        # off-thread send the request-time dispatcher uses.
        dispatcher = PushDispatcher(
            devices=c.push_device_repository,
            sender=c.push_sender,
            preferences=c.notification_preference_repository,
            locale=app.config.get("PUSH_LOCALE", "vi"),
            run_async=False,
            events=c.notification_event_repository,
        )
        usecase = SendScheduledRemindersUseCase(
            source=SQLAlchemyReminderSource(
                session, c.get_monthly_labor_summary_usecase, c.get_labor_payments_summary_usecase
            ),
            log=SQLAlchemyReminderLog(session),
            dispatcher=dispatcher,
            events=c.notification_event_repository,
            preferences=c.notification_preference_repository,
        )
        report = usecase.execute(at, dry_run=args.dry_run)
        names = _people(session, [p.user_id for p in report.planned])

    mode = "DRY RUN" if args.dry_run else "run"
    log.info(
        "%s at %s (Europe/Paris): due=%s recipients=%d",
        mode,
        report.local_now.strftime("%a %Y-%m-%d %H:%M %Z"),
        ",".join(report.due) or "nothing",
        len(report.planned),
    )
    for p in report.planned:
        who = names.get(p.user_id, str(p.user_id))
        if p.action == "already_sent":
            log.info("  %s: %s already sent today, skipped", who, "+".join(p.kinds))
            continue
        title, body = p.texts.get("vi", ("", ""))
        log.info("  %s [%s] %s: %s — %s  data=%s", who, p.action, "+".join(p.kinds), title, body, p.data)
        for locale in ("fr", "en"):
            t, b = p.texts.get(locale, ("", ""))
            log.info("      %s: %s — %s", locale, t, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
