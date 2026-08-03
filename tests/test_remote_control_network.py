"""Remote Control listener binding and interface-discovery tests."""

from types import SimpleNamespace

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.gateway import network


def test_frontdoor_binds_loopback_when_remote_control_is_disabled():
    cfg = AletheiaConfig()
    cfg.app.bind_host = "0.0.0.0"
    assert network.frontdoor_bind_hosts(cfg) == ["127.0.0.1"]
    assert network.backend_bind_host() == "127.0.0.1"


def test_frontdoor_binds_only_selected_remote_control_hosts():
    cfg = AletheiaConfig()
    cfg.remote_control.enabled = True
    cfg.remote_control.bind_hosts = ["100.64.0.8", "10.42.0.8"]
    assert network.frontdoor_bind_hosts(cfg) == ["100.64.0.8", "10.42.0.8"]


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::1", "not-an-ip"])
def test_remote_control_rejects_non_external_ipv4_hosts(host):
    with pytest.raises(ValueError):
        network.normalize_remote_bind_hosts([host])


def test_interface_discovery_returns_non_loopback_ipv4_addresses(monkeypatch):
    monkeypatch.setattr(network.psutil, "net_if_stats", lambda: {
        "Ethernet": SimpleNamespace(isup=True),
        "EasyTier": SimpleNamespace(isup=False),
    })
    monkeypatch.setattr(network.psutil, "net_if_addrs", lambda: {
        "lo": [SimpleNamespace(family=network.socket.AF_INET, address="127.0.0.1", netmask="255.0.0.0")],
        "Ethernet": [SimpleNamespace(family=network.socket.AF_INET, address="192.168.1.10", netmask="255.255.255.0")],
        "EasyTier": [SimpleNamespace(family=network.socket.AF_INET, address="100.64.0.8", netmask="255.255.255.0")],
    })

    assert network.discover_ipv4_interfaces() == [
        {"name": "EasyTier", "address": "100.64.0.8", "netmask": "255.255.255.0", "is_up": False},
        {"name": "Ethernet", "address": "192.168.1.10", "netmask": "255.255.255.0", "is_up": True},
    ]
