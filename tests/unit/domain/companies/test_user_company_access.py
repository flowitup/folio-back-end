"""Unit tests for UserCompanyAccess domain entity."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.companies.user_company_access import UserCompanyAccess


def _make_access(**overrides) -> UserCompanyAccess:
    defaults = dict(
        user_id=uuid4(),
        company_id=uuid4(),
        is_primary=False,
        attached_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return UserCompanyAccess(**defaults)


class TestUserCompanyAccessWithUpdates:
    def test_set_primary(self):
        a = _make_access(is_primary=False)
        updated = a.with_updates(is_primary=True)
        assert updated.is_primary is True
        assert a.is_primary is False  # original unchanged

    def test_preserves_composite_key(self):
        uid = uuid4()
        cid = uuid4()
        a = _make_access(user_id=uid, company_id=cid)
        updated = a.with_updates(is_primary=True)
        assert updated.user_id == uid
        assert updated.company_id == cid


class TestUserCompanyAccessEquality:
    def test_equal_same_composite_key(self):
        uid, cid = uuid4(), uuid4()
        a1 = _make_access(user_id=uid, company_id=cid, is_primary=False)
        a2 = _make_access(user_id=uid, company_id=cid, is_primary=True)
        assert a1 == a2

    def test_not_equal_different_user(self):
        cid = uuid4()
        a1 = _make_access(user_id=uuid4(), company_id=cid)
        a2 = _make_access(user_id=uuid4(), company_id=cid)
        assert a1 != a2

    def test_not_equal_different_company(self):
        uid = uuid4()
        a1 = _make_access(user_id=uid, company_id=uuid4())
        a2 = _make_access(user_id=uid, company_id=uuid4())
        assert a1 != a2

    def test_hash_consistent_with_equality(self):
        uid, cid = uuid4(), uuid4()
        a1 = _make_access(user_id=uid, company_id=cid, is_primary=False)
        a2 = _make_access(user_id=uid, company_id=cid, is_primary=True)
        assert hash(a1) == hash(a2)

    def test_not_equal_to_non_access(self):
        a = _make_access()
        assert a.__eq__("not an access") is NotImplemented
