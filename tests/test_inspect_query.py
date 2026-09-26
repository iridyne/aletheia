"""`inspect query` / `inspect tables`: read-only debugging surface.

These replace hand-written `python -c "... get_db().execute(...)"` probes, so the
security-critical invariant is that the engine refuses writes.
"""

import json

import pytest
from typer.testing import CliRunner

from aletheia.cli.inspect_cmd import inspect_app

# ponytail: the gate is table-agnostic, so a scratch table avoids seeding the
# whole universe_members -> snapshots -> outcomes FK chain just to read one row.
_PROBE_TABLE = "inspect_query_probe"


def _insert(db, row_id: str, *, symbol: str, horizon: str, status: str) -> None:
    db.execute(
        f"INSERT INTO {_PROBE_TABLE} (id, symbol, horizon, status) VALUES (?, ?, ?, ?)",
        (row_id, symbol, horizon, status),
    )


@pytest.fixture
def seeded_db(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db, reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    run_migrations()
    db = get_db()
    db.execute(
        f"CREATE TABLE {_PROBE_TABLE} "
        "(id TEXT PRIMARY KEY, symbol TEXT, horizon TEXT, status TEXT)"
    )
    _insert(db, "row-1", symbol="BTC/USDT", horizon="15m", status="settled")
    db.commit()
    yield
    reset_db()


@pytest.mark.parametrize(
    "statement",
    [
        f"DELETE FROM {_PROBE_TABLE}",
        f"UPDATE {_PROBE_TABLE} SET status='tampered'",
        f"INSERT INTO {_PROBE_TABLE} (id) VALUES ('x')",
        f"DROP TABLE {_PROBE_TABLE}",
    ],
)
def test_query_refuses_every_mutating_statement(seeded_db, statement):
    result = CliRunner().invoke(inspect_app, ["query", statement])
    assert result.exit_code != 0
    assert "readonly database" in result.output

    from aletheia.storage.sqlite import get_db

    surviving = get_db().execute(
        f"SELECT status FROM {_PROBE_TABLE} WHERE id='row-1'"
    ).fetchone()
    assert surviving[0] == "settled"


def test_query_returns_rows_with_parameters(seeded_db):
    result = CliRunner().invoke(
        inspect_app,
        [
            "query",
            f"SELECT symbol, horizon FROM {_PROBE_TABLE} WHERE symbol = ?",
            "--param",
            "BTC/USDT",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["columns"] == ["symbol", "horizon"]
    assert payload["rows"] == [{"symbol": "BTC/USDT", "horizon": "15m"}]
    assert payload["truncated"] is False


def test_query_reports_limit_truncation(seeded_db):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(3):
        _insert(db, f"extra-{index}", symbol="ETH/USDT", horizon="1h", status="settled")
    db.commit()

    result = CliRunner().invoke(
        inspect_app,
        ["query", f"SELECT id FROM {_PROBE_TABLE}", "--limit", "2", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["rows"]) == 2
    assert payload["truncated"] is True


def test_query_rejects_a_statement_without_a_result_set(seeded_db):
    result = CliRunner().invoke(inspect_app, ["query", "BEGIN"])
    assert result.exit_code != 0


def test_tables_lists_counts_and_one_table_columns(seeded_db):
    listing = CliRunner().invoke(
        inspect_app, ["tables", "--like", _PROBE_TABLE, "--json"]
    )
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.stdout) == [{"table": _PROBE_TABLE, "rows": 1}]

    detail = CliRunner().invoke(
        inspect_app, ["tables", "--table", _PROBE_TABLE, "--json"]
    )
    assert detail.exit_code == 0, detail.output
    payload = json.loads(detail.stdout)
    assert payload["rows"] == 1
    assert "horizon" in {column["name"] for column in payload["columns"]}

    missing = CliRunner().invoke(inspect_app, ["tables", "--table", "no_such_table"])
    assert missing.exit_code != 0
