from __future__ import annotations

import json

from typer.testing import CliRunner

from aletheia.cli.network_cmd import network_app
from aletheia.config.schema import AletheiaConfig

runner = CliRunner()


def _setup_config(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(jsonl, "_last_audit_hash", None)
    monkeypatch.setattr("aletheia.net.apply_proxy_env", lambda: None)
    return loader


def test_network_status_shows_config(tmp_path, monkeypatch):
    loader = _setup_config(tmp_path, monkeypatch)
    cfg = AletheiaConfig()
    cfg.network.proxy_protocol = "http"
    cfg.network.proxy_host = "user:secret@127.0.0.1"
    cfg.network.proxy_port = 7890
    cfg.network.no_proxy = "example.internal,10.0.0.0/8"
    cfg.network.recompose_proxy_url()
    loader.save_config(cfg)

    result = runner.invoke(network_app, ["status"])

    assert result.exit_code == 0
    assert "Network proxy configuration" in result.output
    assert "Protocol: http" in result.output
    assert "Host: 127.0.0.1" in result.output
    assert "Effective proxy: http://127.0.0.1:7890" in result.output
    assert "Configured: yes" in result.output
    assert "secret" not in result.output


def test_network_set_persists_fields(tmp_path, monkeypatch):
    loader = _setup_config(tmp_path, monkeypatch)

    result = runner.invoke(
        network_app,
        ["set", "--protocol", "socks5", "--host", "127.0.0.1", "--port", "7891", "--enforce"],
    )

    assert result.exit_code == 0
    cfg = loader.load_config()
    assert cfg.network.proxy_protocol == "socks5"
    assert cfg.network.proxy_host == "127.0.0.1"
    assert cfg.network.proxy_port == 7891
    assert cfg.network.enforce is True
    assert cfg.network.proxy_url == "socks5://127.0.0.1:7891"
    assert "SOCKS5 support requires aiohttp-socks" in result.output


def test_network_set_rejects_bad_port(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch)

    result = runner.invoke(network_app, ["set", "--port", "99999"])

    assert result.exit_code != 0
    assert "--port must be between 1 and 65535" in result.output


def test_network_disable_clears_proxy(tmp_path, monkeypatch):
    loader = _setup_config(tmp_path, monkeypatch)
    cfg = AletheiaConfig()
    cfg.network.proxy_host = "127.0.0.1"
    cfg.network.proxy_port = 7890
    cfg.network.recompose_proxy_url()
    loader.save_config(cfg)

    result = runner.invoke(network_app, ["disable"])

    assert result.exit_code == 0
    cfg = loader.load_config()
    assert cfg.network.proxy_host == ""
    assert cfg.network.proxy_url == ""

    status_result = runner.invoke(network_app, ["status"])
    assert status_result.exit_code == 0
    assert "Effective proxy: disabled (direct connection)" in status_result.output
    assert "Configured: no" in status_result.output


def test_network_test_reports_reachability(tmp_path, monkeypatch):
    loader = _setup_config(tmp_path, monkeypatch)

    def fake_probe(report):
        from aletheia.config.loader import load_config

        net = load_config().network
        if net.proxy_url:
            report.add(
                "network.proxy",
                "ok",
                f"proxy reachable [fail-open] (42ms) - {net.proxy_url}; egress 1.2.3.4 != direct 5.6.7.8",
            )
        else:
            report.add("network.proxy", "ok", "not configured (direct connection)")

    monkeypatch.setattr("aletheia.diagnostics.doctor._probe_network", fake_probe)

    configured = runner.invoke(network_app, ["test"])
    assert configured.exit_code == 0
    assert "Configured: yes" in configured.output
    assert "Reachable: yes" in configured.output
    assert "42ms" in configured.output

    cfg = loader.load_config()
    cfg.network.proxy_host = ""
    cfg.network.recompose_proxy_url()
    loader.save_config(cfg)

    direct = runner.invoke(network_app, ["test"])
    assert direct.exit_code == 0
    assert "Configured: no" in direct.output
    assert "Reachable: n/a" in direct.output
    assert "not configured (direct connection)" in direct.output


def test_network_set_writes_audit_log(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch)

    result = runner.invoke(network_app, ["set", "--host", "127.0.0.1", "--port", "7890"])

    assert result.exit_code == 0
    audit_path = tmp_path / "logs" / "audit.jsonl"
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(event.get("event_type") == "network_config_changed" for event in events)
