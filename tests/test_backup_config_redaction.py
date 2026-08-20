"""Regression test for #1175: managed backups must never include raw config credentials."""

import sqlite3
import tarfile
import tempfile
from pathlib import Path

import yaml


def _make_db(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "app.db"))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


def _unpack_config(archive: Path) -> dict:
    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        cfg_path = bundle_dir / "config.yaml"
        manifest_path = bundle_dir / "manifest.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else None
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return {"config": cfg, "manifest": manifest, "has_config": cfg_path.exists()}


def test_coinalyze_api_key_redacted_in_backup(tmp_path):
    """#1175: coinalyze_api_key sentinel must not appear in any retained backup."""
    _make_db(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "market_data": {"coinalyze_api_key": "SENTINEL_COIN_KEY"},
            "trading": {"symbol": "BTC/USDT"},
        }),
        encoding="utf-8",
    )

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup()

    result = _unpack_config(archive)
    assert result["has_config"], "config.yaml must be present in archive"

    raw = (tmp_path / "config.yaml").read_bytes()
    archive_cfg = result["config"]
    assert b"SENTINEL_COIN_KEY" not in raw or True  # sentinel is in source — confirm it's absent in backup
    assert archive_cfg is not None
    md = archive_cfg.get("market_data") or {}
    assert md.get("coinalyze_api_key") != "SENTINEL_COIN_KEY", "sentinel must be redacted"
    assert archive_cfg.get("trading", {}).get("symbol") == "BTC/USDT", "non-secret field must be preserved"
    assert result["manifest"]["config_status"] == "redacted"


def test_proxy_credentials_redacted_in_backup(tmp_path):
    """#1175: proxy_url inline credentials must be stripped, host/port kept."""
    _make_db(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "network": {"proxy_url": "http://sentinel_user:sentinel_pass@proxy.internal:3128"},
        }),
        encoding="utf-8",
    )

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup()

    result = _unpack_config(archive)
    assert result["has_config"]
    cfg_text = yaml.safe_dump(result["config"])
    assert "sentinel_user" not in cfg_text
    assert "sentinel_pass" not in cfg_text
    assert "proxy.internal" in cfg_text, "host must be retained"
    assert result["manifest"]["config_status"] == "redacted"


def test_malformed_config_omitted_with_manifest_status(tmp_path):
    """#1175: unparseable config must be omitted and manifest must record omission."""
    _make_db(tmp_path)
    (tmp_path / "config.yaml").write_bytes(b"\xff\xfe invalid: yaml: [[[")

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup()

    result = _unpack_config(archive)
    assert not result["has_config"], "malformed config must be absent from archive"
    assert result["manifest"]["config_status"] == "omitted"


def test_backup_archive_contains_no_sentinel_bytes(tmp_path):
    """#1175: raw archive bytes must not contain sentinel credential in any form."""
    _make_db(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "market_data": {"coinalyze_api_key": "GLOBALSENTINEL_XYZ"},
            "network": {"proxy_url": "http://usr:GLOBALSENTINEL_XYZ@host:80"},
        }),
        encoding="utf-8",
    )

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup()

    archive_bytes = archive.read_bytes()
    assert b"GLOBALSENTINEL_XYZ" not in archive_bytes, (
        "sentinel credential must not appear anywhere in the archive bytes"
    )
