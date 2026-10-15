"""Rate-limit bucket of a client address: an IPv6 client owns a whole /64, so that is the bucket."""

from __future__ import annotations

import pytest
from flask import Flask

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.infrastructure.rate_limiter import client_ip_bucket, remote_address_bucket


def test_ipv4_is_its_own_bucket() -> None:
    assert client_ip_bucket("203.0.113.7") == "203.0.113.7"
    assert client_ip_bucket("203.0.113.8") != client_ip_bucket("203.0.113.7")


def test_addresses_of_one_ipv6_64_share_a_bucket() -> None:
    a = client_ip_bucket("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    b = client_ip_bucket("2001:db8:1:2:1::1")
    assert a == b == "2001:db8:1:2::/64"


def test_different_ipv6_64s_get_different_buckets() -> None:
    assert client_ip_bucket("2001:db8:1:2::1") != client_ip_bucket("2001:db8:1:3::1")


def test_ipv4_mapped_ipv6_maps_to_the_ipv4() -> None:
    assert client_ip_bucket("::ffff:203.0.113.7") == "203.0.113.7"


def test_ipv6_with_a_zone_id_still_maps_to_its_64() -> None:
    assert client_ip_bucket("fe80::1%eth0") == "fe80::/64"


@pytest.mark.parametrize("garbage", ["not-an-ip", "999.1.1.1", "2001:db8::zz"])
def test_garbage_falls_back_to_the_raw_string(garbage: str) -> None:
    assert client_ip_bucket(garbage) == garbage


@pytest.mark.parametrize("missing", [None, ""])
def test_missing_address_keeps_the_old_fallback(missing) -> None:
    assert client_ip_bucket(missing) == "127.0.0.1"


def _key_in_request(key_func, remote_addr: str | None) -> str:
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only-not-a-secret"
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": remote_addr or ""}):
        return key_func()


def test_limiter_key_function_uses_the_64() -> None:
    assert _key_in_request(remote_address_bucket, "2001:db8:9:9::5") == "2001:db8:9:9::/64"
    assert _key_in_request(remote_address_bucket, "203.0.113.7") == "203.0.113.7"


def test_anonymous_jwt_key_falls_back_to_the_64() -> None:
    assert _key_in_request(jwt_user_key, "2001:db8:9:9::5") == "2001:db8:9:9::/64"
    assert _key_in_request(jwt_user_key, None) == "unknown"
