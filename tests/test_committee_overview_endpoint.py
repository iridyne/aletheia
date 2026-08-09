"""Tests for GET /api/committee/reports (AI Jury workbench).

The endpoint is a pure read-only aggregation over the append-only
committee_reports table: parsed evaluations (opinions / evidence / risks)
plus a window summary (direction mix, averages, blocked/fallback counts).

Asserted contracts:
- Empty table returns status="ok" with an empty list and null averages —
  never invented data, never an error.
- Seeded rows come back newest-first with JSON fields parsed into
  structured lists/dicts (same field contract as the decision-report
  committee section consumed by DecisionTraceDrawer).
- symbol filter narrows the window; summary aggregates only the window.
- GET mutates nothing: committee_reports row count is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Isolated DB fixture (mirrors tests/test_decision_report_endpoint.py)
# ---------------------------------------------------------------------------


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    # ALETHEIA_DATA_DIR activates the runtime-home override so importing the
    # server app with an isolated DATA_DIR never trips the legacy-migration guard.
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _seed_reports() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import CommitteeReport

    repo.insert_committee_report(
        CommitteeReport(
            id="cr-jury-older",
            opportunity_id="opp-jury-1",
            symbol="BTC/USDT",
            decision_id="dec-jury-1",
            direction="long",
            confidence=0.62,
            explainability_score=0.71,
            model_used="primary-model",
            indicators_json=json.dumps({"rsi": 48.0}),
            agent_opinions_json=json.dumps([{
                "agent": "LLM",
                "direction": "long",
                "confidence": 0.62,
                "reasoning": "volume expansion above range",
                "evidence": [{"indicator": "RSI", "value": "RSI 48", "contribution": 10.0}],
            }]),
            risks_json=json.dumps(["funding could spike"]),
            created_at="2026-08-01T09:00:00",
        )
    )
    repo.insert_committee_report(
        CommitteeReport(
            id="cr-jury-newer",
            opportunity_id="opp-jury-2",
            symbol="ETH/USDT",
            decision_id="dec-jury-2",
            direction="neutral",
            confidence=0.40,
            explainability_score=0.55,
            blocked=1,
            fallback_used=1,
            model_used="fallback-model",
            agent_opinions_json=json.dumps([]),
            risks_json=json.dumps([]),
            created_at="2026-08-02T09:00:00",
        )
    )


async def _get_reports(**params):
    from httpx import ASGITransport, AsyncClient

    from aletheia.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/committee/reports", params=params)


def _committee_count() -> int:
    from aletheia.storage.sqlite import get_db

    return get_db().execute("SELECT count(*) FROM committee_reports").fetchone()[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_table_is_honest_ok(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    resp = await _get_reports()
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["evaluations"] == []
    summary = body["summary"]
    assert summary["total"] == 0
    assert summary["direction_mix"] == {}
    # Absence stays null — never coerced to a benign 0.0.
    assert summary["avg_confidence"] is None
    assert summary["avg_explainability"] is None
    assert summary["latest_at"] is None


@pytest.mark.anyio
async def test_seeded_rows_parsed_newest_first(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_reports()

    resp = await _get_reports()
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    evals = body["evaluations"]
    assert [e["id"] for e in evals] == ["cr-jury-newer", "cr-jury-older"]

    older = evals[1]
    assert older["symbol"] == "BTC/USDT"
    assert older["opportunity_id"] == "opp-jury-1"
    assert older["direction"] == "long"
    assert older["confidence"] == pytest.approx(0.62)
    assert older["explainability_score"] == pytest.approx(0.71)
    assert older["blocked"] is False
    assert older["fallback_used"] is False
    # JSON columns arrive parsed, matching the decision-report contract.
    assert older["indicators"] == {"rsi": 48.0}
    assert older["agent_opinions"][0]["reasoning"] == "volume expansion above range"
    assert older["agent_opinions"][0]["evidence"][0]["value"] == "RSI 48"
    assert older["risks"] == ["funding could spike"]

    newer = evals[0]
    assert newer["blocked"] is True
    assert newer["fallback_used"] is True
    assert newer["agent_opinions"] == []

    summary = body["summary"]
    assert summary["total"] == 2
    assert summary["direction_mix"] == {"long": 1, "neutral": 1}
    assert summary["avg_confidence"] == pytest.approx(0.51)
    assert summary["avg_explainability"] == pytest.approx(0.63)
    assert summary["blocked_count"] == 1
    assert summary["fallback_count"] == 1
    assert summary["models"] == {"primary-model": 1, "fallback-model": 1}
    assert summary["symbol_count"] == 2
    assert summary["latest_at"] == "2026-08-02T09:00:00"


@pytest.mark.anyio
async def test_symbol_filter_and_limit(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_reports()

    resp = await _get_reports(symbol="BTC/USDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["window"]["symbol"] == "BTC/USDT"
    evals = body["evaluations"]
    assert len(evals) == 1
    assert evals[0]["id"] == "cr-jury-older"
    # Summary aggregates only the filtered window.
    assert body["summary"]["direction_mix"] == {"long": 1}
    assert body["summary"]["blocked_count"] == 0

    limited = await _get_reports(limit=1)
    limited_body = limited.json()
    assert [e["id"] for e in limited_body["evaluations"]] == ["cr-jury-newer"]


@pytest.mark.anyio
async def test_get_is_read_only(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_reports()
    before = _committee_count()

    await _get_reports()
    await _get_reports(symbol="ETH/USDT")

    assert _committee_count() == before == 2
