"""Command line for the QA personas (see scripts/qa_personas.py).

    QA_PASSWORD='…' uv run python -m scripts.qa_personas_cli --create --suffix smoke \\
        --admin-email qa.admin@example.com \\
        --manager-email qa.manager@example.com \\
        --member-email qa.member@example.com

    uv run python -m scripts.qa_personas_cli --delete --suffix smoke --dry-run \\
        --admin-email ... --manager-email ... --member-email ...

The password is read from the ``QA_PASSWORD`` environment variable, never from
argv: this runs on production hosts, where argv is visible in `ps` and lands in
shell history.
"""

from __future__ import annotations

import argparse
import os
import sys

from scripts.qa_personas import DEFAULT_ADDRESS, company_name, create_personas
from scripts.qa_personas_purge import purge_company

_PASSWORD_ENV = "QA_PASSWORD"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="create (or find) the personas")
    mode.add_argument("--delete", action="store_true", help="delete everything tagged with the suffix")
    parser.add_argument("--suffix", required=True, help="tag for this persona set, e.g. 'smoke'")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--manager-email", required=True)
    parser.add_argument("--member-email", required=True)
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="company address")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --delete: count the rows per table and roll back, deleting nothing",
    )
    return parser.parse_args(argv)


def _create(args, emails: dict[str, str], password: str) -> int:
    ids = create_personas(args.suffix, emails, password, args.address)
    print(f"QA personas ready for '{company_name(args.suffix)}':")
    for key, value in ids.items():
        print(f"  {key} = {value}")
    for role, email in emails.items():
        print(f"  {role}: {email}")
    return 0


def _delete(args, emails: dict[str, str]) -> int:
    counts = purge_company(company_name(args.suffix), list(emails.values()), dry_run=args.dry_run)
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} QA data for '{company_name(args.suffix)}':")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    if not counts:
        print("  (nothing to delete)")
    return 0


def main(argv: list[str] | None = None) -> int:
    from app import create_app

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.suffix.strip():
        print("--suffix must not be blank", file=sys.stderr)
        return 2
    password = os.environ.get(_PASSWORD_ENV, "")
    if args.create and not password:
        print(f"--create requires the {_PASSWORD_ENV} environment variable", file=sys.stderr)
        return 2
    if args.dry_run and not args.delete:
        print("--dry-run only applies to --delete", file=sys.stderr)
        return 2

    emails = {"admin": args.admin_email, "manager": args.manager_email, "member": args.member_email}

    app = create_app()
    with app.app_context():
        try:
            return _create(args, emails, password) if args.create else _delete(args, emails)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
