"""Network-interface discovery and listener binding for Remote Control."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable

import psutil

_LOOPBACK_HOST = "127.0.0.1"


def discover_ipv4_interfaces() -> list[dict[str, object]]:
    """Return IPv4 addresses that an operator may expose through Remote Control.

    The result intentionally excludes loopback because the gateway always keeps that
    listener for local operation. Inactive adapters remain visible so an operator can
    understand why a configured interface is currently unavailable.
    """
    stats = psutil.net_if_stats()
    interfaces: list[dict[str, object]] = []
    for name, addresses in psutil.net_if_addrs().items():
        is_up = bool(stats.get(name) and stats[name].isup)
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            host = str(address.address)
            if host.startswith("127."):
                continue
            interfaces.append({
                "name": name,
                "address": host,
                "netmask": str(address.netmask or ""),
                "is_up": is_up,
            })
    return sorted(interfaces, key=lambda item: (str(item["name"]), str(item["address"])))


def normalize_remote_bind_hosts(hosts: Iterable[str]) -> list[str]:
    """Validate and deduplicate selected IPv4 listener addresses."""
    normalized: list[str] = []
    for raw in hosts:
        host = str(raw).strip()
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(f"Remote Control address is not a valid IP: {host}") from exc
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            raise ValueError(f"Remote Control address must be a non-loopback IPv4 address: {host}")
        if host not in normalized:
            normalized.append(host)
    return normalized


def frontdoor_bind_hosts(cfg) -> list[str]:
    """Resolve the public frontdoor listeners; fail closed to loopback by default."""
    remote_control = getattr(cfg, "remote_control", None)
    if not getattr(remote_control, "enabled", False):
        return [_LOOPBACK_HOST]
    return [_LOOPBACK_HOST, *normalize_remote_bind_hosts(getattr(remote_control, "bind_hosts", []))]


def local_frontdoor_host() -> str:
    """Loopback address for local lifecycle, health, and browser operations."""
    return _LOOPBACK_HOST


def backend_bind_host() -> str:
    """The inner API worker is never a Remote Control listener."""
    return _LOOPBACK_HOST
