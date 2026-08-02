import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET


def test_source_checkout_cannot_install_machine_service(monkeypatch):
    from aletheia.gateway import windows_machine_service as service
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(service, "_runtime_is_service_safe", lambda *args, **kwargs: False)
    result = service.install()
    assert result["ok"] is False
    assert result["reason"] == "packaged_runtime_required"


def test_winsw_config_is_passwordless_least_privilege_and_machine_scoped(monkeypatch, tmp_path):
    from aletheia.gateway import windows_machine_service as service
    monkeypatch.setattr(service, "machine_data_dir", lambda: tmp_path / "runtime")
    xml = service._build_winsw_xml(executable=tmp_path / "Program Files" / "aletheia.exe")
    root = ET.fromstring(xml)
    account = root.find("serviceaccount")
    assert account is not None
    assert account.findtext("username") == r"NT AUTHORITY\LocalService"
    assert account.find("password") is None
    assert "ALETHEIA_DATA_DIR" in xml
    assert "__packaged_gateway_runner --no-browser" in xml
    assert "action=\"restart\"" in xml


def test_elevation_request_contains_no_password_or_secret(tmp_path, monkeypatch):
    from aletheia.gateway import windows_machine_service as service
    monkeypatch.setattr(service, "_request_dir", lambda: tmp_path)
    wrapper = tmp_path / f"WinSW-x64-{service.WINSW_VERSION}.exe"
    wrapper.write_bytes(b"wrapper")
    path = service._write_request("install", wrapper=wrapper)
    payload = path.read_text(encoding="utf-8")
    assert "password" not in payload.lower()
    assert "secret" not in payload.lower()
    assert json.loads(payload)["operation"] == "install"


def test_firewall_elevation_request_contains_only_validated_port(tmp_path, monkeypatch):
    from aletheia.gateway import windows_machine_service as service

    monkeypatch.setattr(service, "_request_dir", lambda: tmp_path)
    path = service._write_request("firewall_enable_lan", arguments={"port": 8787})
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["operation"] == "firewall_enable_lan"
    assert payload["arguments"] == {"port": 8787}
    assert "8788" not in path.read_text(encoding="utf-8")


def test_source_checkout_uac_uses_installed_cli_launcher(tmp_path, monkeypatch):
    from aletheia.gateway import windows_machine_service as service

    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    cli = scripts / "aletheia.exe"
    python.write_bytes(b"python")
    cli.write_bytes(b"cli")
    monkeypatch.setattr(service.sys, "executable", str(python))
    monkeypatch.setattr(service, "is_frozen_runtime", lambda: False)

    executable, prefix = service._elevated_launcher()

    assert executable == cli.resolve()
    assert prefix == []


def test_firewall_elevated_operation_rejects_unexpected_arguments():
    from aletheia.gateway import windows_machine_service as service

    result = service._apply_firewall_operation({
        "operation": "firewall_enable_lan",
        "arguments": {"port": 8787, "program": r"C:\untrusted.exe"},
    })

    assert result["ok"] is False
    assert result["reason"] == "invalid_elevation_request"


def test_firewall_elevated_operation_cannot_open_unconfigured_port(monkeypatch):
    from aletheia.config import loader
    from aletheia.gateway import windows_firewall
    from aletheia.gateway import windows_machine_service as service

    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(app=SimpleNamespace(bind_host="0.0.0.0", dashboard_port=8787)),
    )
    monkeypatch.setattr(
        windows_firewall,
        "apply_enable_lan",
        lambda port: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    result = service._apply_firewall_operation({
        "operation": "firewall_enable_lan",
        "arguments": {"port": 22},
    })

    assert result["ok"] is False
    assert result["reason"] == "invalid_elevation_request"


def test_no_elevate_is_actionable_and_non_mutating(monkeypatch):
    from aletheia.gateway import windows_machine_service as service
    result = service._elevated("start", elevate=False)
    assert result["ok"] is False
    assert result["reason"] == "elevation_required"


def test_uninstall_refuses_running_machine_service(monkeypatch):
    from aletheia.gateway import windows_machine_service as service
    monkeypatch.setattr(service, "status", lambda: {"installed": True, "running": True, "owned": True})
    assert service.uninstall()["reason"] == "service_running"


def test_pinned_winsw_asset_contract():
    from aletheia.gateway import windows_machine_service as service
    assert service.WINSW_URL.startswith("https://github.com/winsw/winsw/releases/download/v2.12.0/")
    assert len(service.WINSW_SHA256) == 64
    int(service.WINSW_SHA256, 16)
