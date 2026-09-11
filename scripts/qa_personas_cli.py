"""Command line for the QA personas (see scripts/qa_personas.py).

    uv run python -m scripts.qa_personas_cli --create --suffix smoke \
        --admin-email qa.admin@example.com --admin-phone +33600000101 \
        --manager-email qa.manager@example.com --manager-phone +33600000102 \
        --member-email qa.member@example.com --member-phone +33600000103

    uv run python -m scripts.qa_personas_cli --delete --suffix smoke --dry-run \
        --admin-email ... --manager-email ... --member-email ...

Phone numbers are CLI args, not an environment variable: unlike the password
this replaced, a phone number is not a secret — it is the SMS code, sent to
that number, that proves anything.
"""

from __future__ import annotations

import argparse
import sys

from scripts.qa_personas import DEFAULT_ADDRESS, company_name, create_personas
from scripts.qa_personas_purge import purge_company


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="create (or find) the personas")
    mode.add_argument("--delete", action="store_true", help="delete everything tagged with the suffix")
    parser.add_argument("--suffix", required=True, help="tag for this persona set, e.g. 'smoke'")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--manager-email", required=True)
    parser.add_argument("--member-email", required=True)
    parser.add_argument("--admin-phone", help="required with --create; French E.164, e.g. +33600000101")
    parser.add_argument("--manager-phone", help="required with --create")
    parser.add_argument("--member-phone", help="required with --create")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="company address")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --delete: count the rows per table and roll back, deleting nothing",
    )
    return parser.parse_args(argv)


def _create(args, emails: dict[str, str], phones: dict[str, str]) -> int:
    ids = create_personas(args.suffix, emails, phones, args.address)
    print(f"QA personas ready for '{company_name(args.suffix)}':")
    for key, value in ids.items():
        print(f"  {key} = {value}")
    for role, email in emails.items():
        print(f"  {role}: {email} ({phones[role]})")
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
    phones = {"admin": args.admin_phone, "manager": args.manager_phone, "member": args.member_phone}
    if args.create and not all(phones.values()):
        print("--create requires --admin-phone, --manager-phone and --member-phone", file=sys.stderr)
        return 2
    if args.dry_run and not args.delete:
        print("--dry-run only applies to --delete", file=sys.stderr)
        return 2

    emails = {"admin": args.admin_email, "manager": args.manager_email, "member": args.member_email}

    app = create_app()
    with app.app_context():
        try:
            return _create(args, emails, phones) if args.create else _delete(args, emails)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
