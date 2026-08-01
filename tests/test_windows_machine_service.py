import json
from pathlib import Path
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
