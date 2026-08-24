"""Phase 1 regression: accidental external unit-test I/O must fail immediately."""

from __future__ import annotations

import socket

import pytest


def test_external_dns_is_denied_with_an_actionable_error() -> None:
    with pytest.raises(socket.gaierror, match="attempted external network access"):
        socket.getaddrinfo("example.com", 443)


def test_external_tcp_helper_is_denied_before_connecting() -> None:
    with pytest.raises(socket.gaierror, match="attempted external network access"):
        socket.create_connection(("203.0.113.1", 443), timeout=1)


def test_loopback_resolution_remains_available_for_local_test_servers() -> None:
    addresses = socket.getaddrinfo("127.0.0.1", 0)
    assert addresses
