"""Bounds of the shared ?limit=&offset= parser."""

import pytest

from app.api._helpers.pagination import MAX_OFFSET, parse_limit_offset


def test_defaults_and_clamp():
    assert parse_limit_offset({}) == (50, 0)
    assert parse_limit_offset({"limit": "1000", "offset": "5"}) == (200, 5)
    assert parse_limit_offset({"offset": str(MAX_OFFSET)}) == (50, MAX_OFFSET)


@pytest.mark.parametrize(
    "args",
    [
        {"limit": "abc"},
        {"limit": "-1"},
        {"limit": "0"},
        {"offset": "-1"},
        {"offset": str(MAX_OFFSET + 1)},
        {"offset": "99999999999999999999"},
    ],
)
def test_out_of_range_values_are_refused(args):
    with pytest.raises(ValueError):
        parse_limit_offset(args)
