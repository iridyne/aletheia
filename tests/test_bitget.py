"""Tests for Bitget adapter: auth signing, demo header, live blocking."""

from aletheia.exchanges.bitget.auth import sign_request, build_headers, get_timestamp


def test_sign_request_deterministic():
    sig1 = sign_request("1000000", "GET", "/api/v2/spot/account/info", "", "my_secret")
    sig2 = sign_request("1000000", "GET", "/api/v2/spot/account/info", "", "my_secret")
    assert sig1 == sig2
    assert len(sig1) > 10  # base64 encoded


def test_sign_request_changes_with_secret():
    sig1 = sign_request("1000000", "GET", "/path", "", "secret_a")
    sig2 = sign_request("1000000", "GET", "/path", "", "secret_b")
    assert sig1 != sig2


def test_build_headers_demo():
    headers = build_headers("key", "secret", "pass", "GET", "/path", demo=True)
    assert headers["ACCESS-KEY"] == "key"
    assert headers["ACCESS-PASSPHRASE"] == "pass"
    assert headers["paptrading"] == "1"
    assert "ACCESS-SIGN" in headers
    assert "ACCESS-TIMESTAMP" in headers


def test_build_headers_live_no_demo():
    headers = build_headers("key", "secret", "pass", "GET", "/path", demo=False)
    assert "paptrading" not in headers


def test_timestamp_is_milliseconds():
    ts = get_timestamp()
    assert len(ts) == 13  # milliseconds timestamp


def test_sign_includes_body():
    sig_empty = sign_request("1000", "POST", "/order", "", "secret")
    sig_body = sign_request("1000", "POST", "/order", '{"symbol":"BTCUSDT"}', "secret")
    assert sig_empty != sig_body
