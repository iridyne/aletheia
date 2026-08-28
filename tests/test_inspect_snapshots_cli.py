from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

import aletheia.storage.repositories as repositories
from aletheia.cli.inspect_cmd import inspect_app


def test_snapshots_symbol_filter_honors_stale_only(monkeypatch):
    now = datetime.now(timezone.utc)
    calls = []
    monkeypatch.setattr(
        repositories,
        "list_market_snapshots",
        lambda **kwargs: calls.append(kwargs) or [
            {"symbol": "BTC/USDT", "price": 100, "exchange": "bitget", "snapshot_at": (now - timedelta(minutes=5)).isoformat()},
            {"symbol": "BTC/USDT", "price": 99, "exchange": "bitget", "snapshot_at": (now - timedelta(hours=2)).isoformat()},
            {"symbol": "BTC/USDT", "price": 98, "exchange": "bitget", "snapshot_at": (now - timedelta(hours=3)).isoformat()},
        ],
    )

    result = CliRunner().invoke(
        inspect_app,
        ["snapshots", "--symbol", "BTC/USDT", "--stale-only", "--limit", "1", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["price"] == 99
    assert payload[0]["is_stale"] is True
    assert calls == [{"symbol": "BTC/USDT", "limit": None}]
