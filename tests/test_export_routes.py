"""Tests for /api/export routes (catalog + download)."""

import csv
import io
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """TestClient with the in-memory test DB made accessible to all threads.

    The conftest _inmemory_db fixture injects the connection via a thread-local,
    but run_in_threadpool and sync FastAPI route threads don't share that local.
    We monkeypatch get_db() to always return the test thread's connection so
    export routes (which call get_db() once per request) hit the same in-memory DB.
    """
    import aletheia.storage.sqlite as sq
    test_conn = sq.get_db()
    monkeypatch.setattr(sq, "get_db", lambda: test_conn)

    from aletheia.server.app import app
    return TestClient(app)


# ─── catalog ──────────────────────────────────────────────────────────────────

def test_catalog_returns_all_datasets(client):
    resp = client.get("/api/export/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "datasets" in data
    tables = {d["table"] for d in data["datasets"]}
    # Core trading tables must be present
    for expected in ("trade_proposals", "risk_decisions", "orders", "fills", "positions"):
        assert expected in tables, f"{expected} missing from catalog"


def test_catalog_returns_row_counts(client):
    resp = client.get("/api/export/catalog")
    data = resp.json()
    for entry in data["datasets"]:
        # Empty in-memory DB: all counts should be 0 (or -1 on error, which we don't expect)
        assert entry["row_count"] >= 0, f"{entry['table']} returned negative row_count"


def test_catalog_includes_preset_membership(client):
    resp = client.get("/api/export/catalog")
    data = resp.json()
    entry_map = {d["table"]: d for d in data["datasets"]}

    # trade_proposals should be in both presets
    assert "analysis" in entry_map["trade_proposals"]["presets"]
    assert "migration" in entry_map["trade_proposals"]["presets"]

    # audit_events should be migration-only
    assert "analysis" not in entry_map["audit_events"]["presets"]
    assert "migration" in entry_map["audit_events"]["presets"]


# ─── download — preset validation ─────────────────────────────────────────────

def test_download_invalid_preset_returns_400(client):
    resp = client.post("/api/export/download", json={"preset": "nonexistent"})
    assert resp.status_code == 400


def test_download_no_preset_no_datasets_returns_400(client):
    resp = client.post("/api/export/download", json={})
    assert resp.status_code == 400


def test_download_empty_include_datasets_returns_400(client):
    resp = client.post("/api/export/download", json={"include_datasets": []})
    assert resp.status_code == 400


def test_download_unknown_dataset_returns_400(client):
    resp = client.post("/api/export/download", json={"include_datasets": ["nonexistent_table"]})
    assert resp.status_code == 400
    assert "nonexistent_table" in resp.text


# ─── download — analysis preset ───────────────────────────────────────────────

def _unzip(content: bytes) -> dict[str, bytes]:
    """Return {filename: bytes} for all files in a ZIP."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_download_analysis_preset_returns_zip(client):
    resp = client.post("/api/export/download", json={"preset": "analysis"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    cd = resp.headers.get("content-disposition", "")
    assert "aletheia_analysis_" in cd
    assert cd.endswith('.zip"')


def test_download_analysis_preset_contains_expected_files(client):
    resp = client.post("/api/export/download", json={"preset": "analysis"})
    files = _unzip(resp.content)
    expected = {
        "trade_proposals.csv", "risk_decisions.csv", "orders.csv",
        "fills.csv", "positions.csv", "capital_events.csv", "paper_account_events.csv",
    }
    assert expected == set(files.keys()), f"Unexpected file set: {set(files.keys())}"


def test_download_analysis_preset_no_config_yaml(client):
    resp = client.post("/api/export/download", json={"preset": "analysis"})
    files = _unzip(resp.content)
    assert "config_backup.yaml" not in files


def test_download_analysis_csvs_have_headers(client):
    resp = client.post("/api/export/download", json={"preset": "analysis"})
    files = _unzip(resp.content)
    for filename, content in files.items():
        text = content.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) >= 1, f"{filename} has no header row"
        # Header row must be non-empty strings (column names)
        assert all(col for col in rows[0]), f"{filename} header has empty column name"


# ─── download — migration preset ──────────────────────────────────────────────

def test_download_migration_preset_contains_audit_and_agent_tables(client):
    resp = client.post("/api/export/download", json={"preset": "migration"})
    assert resp.status_code == 200
    files = _unzip(resp.content)
    for expected in ("audit_events.csv", "agent_runs.csv", "agent_run_steps.csv"):
        assert expected in files, f"{expected} missing from migration export"


def test_download_migration_preset_includes_config(client, tmp_path, monkeypatch):
    import aletheia.config.loader as _loader
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  name: TestAletheia\n")
    monkeypatch.setattr(_loader, "CONFIG_PATH", config_file)

    resp = client.post("/api/export/download", json={"preset": "migration", "include_config": True})
    assert resp.status_code == 200
    files = _unzip(resp.content)
    assert "config_backup.yaml" in files
    content = files["config_backup.yaml"].decode("utf-8")
    assert "Secrets redacted" in content
    assert "TestAletheia" in content


def test_download_migration_config_included_even_without_flag(client, tmp_path, monkeypatch):
    """migration preset always includes config backup regardless of include_config flag."""
    import aletheia.config.loader as _loader
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  name: MigrationTest\n")
    monkeypatch.setattr(_loader, "CONFIG_PATH", config_file)

    resp = client.post("/api/export/download", json={"preset": "migration", "include_config": False})
    assert resp.status_code == 200
    files = _unzip(resp.content)
    assert "config_backup.yaml" in files


# ─── download — custom include_datasets ───────────────────────────────────────

def test_download_custom_datasets_subset(client):
    resp = client.post("/api/export/download", json={"include_datasets": ["orders", "fills"]})
    assert resp.status_code == 200
    files = _unzip(resp.content)
    assert set(files.keys()) == {"orders.csv", "fills.csv"}


def test_download_custom_preserves_canonical_order(client):
    """Files in ZIP should be in canonical table order, not input order."""
    # Request in reverse order
    resp = client.post("/api/export/download", json={"include_datasets": ["fills", "orders"]})
    files = _unzip(resp.content)
    names = list(files.keys())
    assert names.index("orders.csv") < names.index("fills.csv")


# ─── config redaction ─────────────────────────────────────────────────────────

def test_config_redaction_simple_keys(tmp_path, monkeypatch):
    import aletheia.config.loader as _loader
    from aletheia.server.routes.export import _redacted_config_yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "app:\n"
        "  name: Aletheia\n"
        "exchange:\n"
        "  api_key: super_secret_key\n"
        "telegram:\n"
        "  token: bot_token_abc123\n"
        "webhook:\n"
        "  secret: my_webhook_secret\n"
    )
    monkeypatch.setattr(_loader, "CONFIG_PATH", config_file)

    result = _redacted_config_yaml()
    assert "super_secret_key" not in result
    assert "bot_token_abc123" not in result
    assert "my_webhook_secret" not in result
    assert "<REDACTED>" in result
    # Non-secret values must be preserved
    assert "Aletheia" in result


def test_config_redaction_prefixed_keys(tmp_path, monkeypatch):
    """Keys like bot_token or coinalyze_api_key must be fully redacted without mangling."""
    import aletheia.config.loader as _loader
    from aletheia.server.routes.export import _redacted_config_yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "market_data:\n"
        "  coinalyze_api_key: ck_abc123\n"
        "telegram:\n"
        "  bot_token: 1234567890:ABCdef\n"
        "webhook:\n"
        "  webhook_secret: whsec_xyz\n"
    )
    monkeypatch.setattr(_loader, "CONFIG_PATH", config_file)

    result = _redacted_config_yaml()
    assert "ck_abc123" not in result
    assert "1234567890:ABCdef" not in result
    assert "whsec_xyz" not in result
    # Keys themselves must still be recognizable (not mangled)
    assert "coinalyze_api_key:" in result
    assert "bot_token:" in result
    assert "webhook_secret:" in result


def test_config_redaction_non_secret_preserved(tmp_path, monkeypatch):
    """Keys that happen to contain 'token' as a prefix (not suffix) are NOT redacted."""
    import aletheia.config.loader as _loader
    from aletheia.server.routes.export import _redacted_config_yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "inference:\n"
        "  inference_tokens_per_minute: 100000\n"
        "app:\n"
        "  name: Aletheia\n"
    )
    monkeypatch.setattr(_loader, "CONFIG_PATH", config_file)

    result = _redacted_config_yaml()
    # 'inference_tokens_per_minute' should NOT be redacted (token is prefix, not suffix key)
    # The key name ends in `_per_minute`, not `token`
    assert "100000" in result


def test_config_redaction_missing_file(tmp_path, monkeypatch):
    import aletheia.config.loader as _loader
    from aletheia.server.routes.export import _redacted_config_yaml

    monkeypatch.setattr(_loader, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
    result = _redacted_config_yaml()
    assert "not found" in result


# ─── data with rows ───────────────────────────────────────────────────────────

def test_download_with_data_rows(client):
    """Insert a row and verify it appears in the export CSV."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO orders (id, symbol, market_type, direction, order_type, quantity, price, "
        "status, execution_mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ord_test_1", "BTC/USDT", "spot", "long", "limit", 0.01, 50000.0, "filled",
         "paper_internal", "2024-01-01T00:00:00"),
    )

    resp = client.post("/api/export/download", json={"include_datasets": ["orders"]})
    assert resp.status_code == 200
    files = _unzip(resp.content)
    text = files["orders.csv"].decode("utf-8")
    assert "ord_test_1" in text
    assert "BTC/USDT" in text


def test_table_csv_writer_streams_cursor_without_fetchall():
    from aletheia.server.routes.export import _write_table_csv

    class Cursor:
        description = (("id",), ("value",))

        def __iter__(self):
            return iter((("one", 1), ("two", 2)))

        def fetchall(self):
            raise AssertionError("streaming export must not materialize all rows")

    class Database:
        def execute(self, _sql):
            return Cursor()

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        _write_table_csv("orders", Database(), zf, "orders.csv")

    files = _unzip(archive.getvalue())
    assert files["orders.csv"].decode("utf-8").splitlines() == [
        "id,value",
        "one,1",
        "two,2",
    ]
