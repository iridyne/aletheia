"""Regression tests #60/#61: export bundles must redact config.yaml secrets and skip nested secret files."""

import copy
import tarfile

import pytest
import yaml

from extensions.sync.exporter import ExportService


def test_export_config_redacts_credentials(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "network": {"proxy_url": "http://myuser:mypassword@127.0.0.1:7890"},
            "market_data": {"coinalyze_api_key": "COINSECRET123"},
            "webhook": {"secret": "WEBHOOK_SENTINEL", "enabled": True},
        }),
        encoding="utf-8",
    )
    svc = ExportService(data_dir=tmp_path)
    archive = svc.export_bundle(mode="config-only")
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path / "unpacked", filter="data")
    contents = {str(p): p.read_bytes() for p in (tmp_path / "unpacked").rglob("*") if p.is_file()}
    for content_bytes in contents.values():
        assert b"WEBHOOK_SENTINEL" not in content_bytes
    content = next(v.decode() for k, v in contents.items() if k.endswith("/config.yaml"))
    manifest = yaml.safe_load(next(v for k, v in contents.items() if k.endswith("/manifest.yaml")))
    assert manifest["secrets_included"] is False
    assert manifest["config_status"] == "redacted"
    assert yaml.safe_load(content)["webhook"]["enabled"] is True
    assert "mypassword" not in content
    assert "myuser" not in content
    assert "COINSECRET123" not in content
    # host/port kept so the exported config is still useful
    assert "127.0.0.1:7890" in content


def test_copy_artifacts_skips_nested_secret_files(tmp_path):
    nested = tmp_path / "artifacts" / "evidence" / "nested"
    nested.mkdir(parents=True)
    (nested / ".env").write_text("API_SECRET=leaked", encoding="utf-8")
    (nested / "server.key").write_text("-----BEGIN KEY-----", encoding="utf-8")
    (nested / "note.md").write_text("ok", encoding="utf-8")

    svc = ExportService(data_dir=tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    svc._copy_artifacts(bundle, ["evidence"])

    names = [p.name for p in (bundle / "artifacts").rglob("*")]
    assert ".env" not in names
    assert "server.key" not in names
    assert "note.md" in names


def test_recursive_redaction_preserves_noncredential_token_fields_and_input():
    from aletheia.config.redact import redact_config_dict

    data = {"nested": [{"Api-Key": "SENTINEL", "ACCESS_token": 12345,
                        "Private Key": "SENTINEL", "password": "SENTINEL"}],
            "model": {"inference_tokens_per_minute": 120000},
            "remote_control": {"require_token": True},
            "market_data": {"observed_stock_tokens": ["rNVDA/USDT"]}}
    original = copy.deepcopy(data)
    redacted = redact_config_dict(data)
    assert redacted["nested"] == [{key: "***REDACTED***" for key in data["nested"][0]}]
    assert redacted["model"] == data["model"]
    assert redacted["remote_control"] == data["remote_control"]
    assert redacted["market_data"] == data["market_data"]
    assert data == original


def test_malformed_config_export_does_not_publish_original(tmp_path):
    (tmp_path / "config.yaml").write_text("secret: WEBHOOK_SENTINEL\ninvalid: [")
    service = ExportService(data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="config_export_failed"):
        service.export_bundle(mode="config-only")
    assert service.config_status == "omitted"
    assert not list((tmp_path / "exports").rglob("config.yaml"))
    assert not list((tmp_path / "exports").glob("*.tar.gz"))
