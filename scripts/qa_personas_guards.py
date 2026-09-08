"""Who `scripts/qa_personas.py --create` is allowed to touch.

This runs against production, where a typo in `--admin-email` would otherwise
hand a real user's account to QA — and `--delete` would then erase it. The rule
is therefore: an email that already exists is only usable when that user is
already attached to this QA company.
"""

from __future__ import annotations

from sqlalchemy import text

from scripts.qa_personas_purge import id_param, id_sql

_ATTACHMENTS_SQL = text(f"SELECT company_id FROM user_company_access WHERE {id_sql('user_id')} = :uid")


class QaPersonaRefused(ValueError):
    """The requested create would have touched an account it does not own."""


def find_user(email: str):
    from app import db
    from app.infrastructure.database.models import UserModel

    return db.session.query(UserModel).filter_by(email=email.lower()).first()


def find_company(name: str):
    from app import db
    from app.infrastructure.database.models import CompanyModel

    return db.session.query(CompanyModel).filter_by(legal_name=name).first()


def attached_company_ids(user_id) -> list:
    """Every company this user is attached to, as stored."""
    from app import db

    return [row[0] for row in db.session.execute(_ATTACHMENTS_SQL, {"uid": id_param(user_id)}).fetchall()]


def refuse_accounts_we_do_not_own(emails: dict[str, str], company, name: str) -> None:
    """Raise unless every pre-existing email is already a member of this QA company."""
    for email in emails.values():
        user = find_user(email)
        if user is None:
            continue
        attached = company is not None and any(
            id_param(cid) == id_param(company.id) for cid in attached_company_ids(user.id)
        )
        if not attached:
            raise QaPersonaRefused(
                f"refusing to use the existing account {email!r}: it is not attached to {name!r}. "
                "Pick an unused address, or attach the user to the QA company first."
            )
