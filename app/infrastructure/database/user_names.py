"""The display names accounts chose for themselves, for people linked to an account.

A linked `Person` keeps the label typed by the first admin who added the number (often
just the phone) and is shared by every company that person works for. Wherever such a
person is listed, the account's own display name is what everyone should see, the same
on web and mobile, and never a label another company typed.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session


def own_display_names(
    session: Session,
    user_ids: Iterable["UUID | str | None"],
    company_ids: "Iterable[UUID | str] | None" = None,
) -> "dict[str, str]":
    """{user_id: display_name} for the given accounts that have a non-blank display name.

    With ``company_ids``, only accounts attached to one of those companies are returned:
    an account's own name is shown to the companies it belongs to, never to one that has
    only typed its number.
    """
    from app.infrastructure.database.models import UserCompanyAccessModel, UserModel

    ids = {UUID(str(u)) for u in user_ids if u}
    if not ids:
        return {}
    query = session.query(UserModel.id, UserModel.display_name).filter(UserModel.id.in_(list(ids)))
    if company_ids is not None:
        companies = [UUID(str(c)) for c in company_ids]
        if not companies:
            return {}
        query = query.filter(
            session.query(UserCompanyAccessModel.user_id)
            .filter(
                UserCompanyAccessModel.user_id == UserModel.id,
                UserCompanyAccessModel.company_id.in_(companies),
            )
            .exists()
        )
    return {str(uid): name.strip() for uid, name in query.all() if name and name.strip()}
