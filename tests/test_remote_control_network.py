"""Remote Control listener binding and interface-discovery tests."""

from types import SimpleNamespace

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.gateway import network


@pytest.mark.parametrize("configured", [None, "remote_control_api_token", "tunnel_api_token"])
def test_token_protection_checks_presence_without_raw_credentials(monkeypatch, configured):
    from fastapi import HTTPException
    from aletheia.config import secrets
    from aletheia.server.routes import settings

    cfg = AletheiaConfig()
    before = cfg.model_dump()
    saved, audited, reads = [], [], []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda value: saved.append(value.model_dump()))
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda value: audited.append(value))
    monkeypatch.setattr(secrets, "get_secret", lambda ref: pytest.fail("settings must not receive a raw token"))
    def fingerprint(ref):
        reads.append(ref)
        return "synthetic-fingerprint" if ref == configured else None
    monkeypatch.setattr(secrets, "get_secret_fingerprint", fingerprint)
    request = settings.RemoteControlConfigUpdate(require_token=True)
    if configured is None:
        with pytest.raises(HTTPException, match="Set an API token"):
            settings.update_remote_control_config(request)
        assert cfg.model_dump() == before
        assert saved == audited == []
    else:
        assert settings.update_remote_control_config(request)["remote_control"]["require_token"] is True
        assert len(saved) == len(audited) == 1
    assert reads == (["remote_control_api_token"] if configured == "remote_control_api_token"
                     else ["remote_control_api_token", "tunnel_api_token"])


def test_frontdoor_binds_loopback_when_remote_control_is_disabled():
    cfg = AletheiaConfig()
    cfg.app.bind_host = "0.0.0.0"
    assert network.frontdoor_bind_hosts(cfg) == ["127.0.0.1"]
    assert network.backend_bind_host() == "127.0.0.1"


def test_frontdoor_keeps_loopback_with_selected_remote_control_hosts():
    cfg = AletheiaConfig()
    cfg.remote_control.enabled = True
    cfg.remote_control.bind_hosts = ["100.64.0.8", "10.42.0.8"]
    assert network.frontdoor_bind_hosts(cfg) == ["127.0.0.1", "100.64.0.8", "10.42.0.8"]


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
