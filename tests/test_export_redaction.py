"""Regression tests #60/#61: export bundles must redact config.yaml secrets and skip nested secret files."""

import yaml

from aletheia.sync.exporter import ExportService


def test_export_config_redacts_credentials(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "network": {"proxy_url": "http://myuser:mypassword@127.0.0.1:7890"},
            "market_data": {"coinalyze_api_key": "COINSECRET123"},
        }),
        encoding="utf-8",
    )
    svc = ExportService(data_dir=tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    svc._export_config(bundle)

    content = (bundle / "config" / "config.yaml").read_text(encoding="utf-8")
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
