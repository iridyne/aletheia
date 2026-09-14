"""Regression tests for bounded prediction probability history."""

import builtins
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from aletheia.storage import repositories as repo


@pytest.mark.parametrize("failed", [False, True])
async def test_remote_history_failure_is_not_successful_empty_history(monkeypatch, failed):
    import aletheia.net as net
    from extensions.prediction.api import get_prediction_market_history

    monkeypatch.setattr(repo, "list_probability_snapshots", lambda *a, **kw: [])
    monkeypatch.setattr(repo, "get_prediction_market_by_id", lambda *a: {"external_market_id": "token-1"})
    response = Mock()
    response.json.return_value = {"history": []}
    client = Mock()
    client.get = AsyncMock(return_value=response)
    if failed:
        client.get.side_effect = TimeoutError("private transport detail")

    @asynccontextmanager
    async def fake_client(**kwargs):
        yield client

    monkeypatch.setattr(net, "httpx_client", fake_client)
    if failed:
        with pytest.raises(HTTPException) as exc:
            await get_prediction_market_history("market-1")
        assert exc.value.status_code == 503
        assert exc.value.detail == "prediction_history_provider_unavailable"
    else:
        assert await get_prediction_market_history("market-1") == {
            "source": "polymarket_api", "points": [],
        }


@pytest.mark.parametrize("has_snapshots", [False, True])
async def test_history_provider_absence_preserves_local_reads(monkeypatch, has_snapshots):
    from extensions.prediction.api import get_prediction_market_history

    snapshot = {"ts": "2024-01-01T00:00:00+00:00", "yes_probability": 0.4}
    monkeypatch.setattr(repo, "list_probability_snapshots", lambda *a, **kw: [snapshot] if has_snapshots else [])
    monkeypatch.setattr(repo, "get_prediction_market_by_id", lambda *a: {"external_market_id": "token-1"})
    original_import = builtins.__import__

    def missing_provider(name, *args, **kwargs):
        if name == "extensions.prediction.polymarket":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_provider)
    if has_snapshots:
        assert await get_prediction_market_history("market-1") == {
            "source": "local",
            "points": [{**snapshot, "no_probability": None, "liquidity": None}],
        }
    else:
        with pytest.raises(HTTPException) as exc:
            await get_prediction_market_history("market-1")
        assert exc.value.status_code == 503
        assert exc.value.detail == "prediction_history_provider_unavailable"


def test_probability_history_uses_latest_bounded_window_in_chronological_order():
    db = repo.get_db()
    db.executemany(
        "INSERT INTO prediction_probability_snapshots "
        "(id, market_id, ts, yes_probability) VALUES (?, ?, ?, ?)",
        [
            ("snapshot-old", "market-1", "2024-01-01T00:00:00+00:00", 0.1),
            ("snapshot-middle", "market-1", "2024-01-02T00:00:00+00:00", 0.2),
            ("snapshot-new", "market-1", "2024-01-03T00:00:00+00:00", 0.3),
        ],
    )
    db.commit()

    snapshots = repo.list_probability_snapshots("market-1", limit=2)

    assert [snapshot["id"] for snapshot in snapshots] == ["snapshot-middle", "snapshot-new"]
    assert [snapshot["ts"] for snapshot in snapshots] == [
        "2024-01-02T00:00:00+00:00",
        "2024-01-03T00:00:00+00:00",
    ]
