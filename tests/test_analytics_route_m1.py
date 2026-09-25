"""Test #1227: analytics routes never return float('inf') in JSON."""
from __future__ import annotations

import json
import math

from aletheia.server.routes.analytics import _safe_pf, get_round1_progress


def test_safe_pf_all_wins():
    result = _safe_pf(100.0, 0.0)
    assert result["unbounded"] is True
    assert result["value"] is None


def test_safe_pf_normal():
    result = _safe_pf(100.0, 50.0)
    assert result["unbounded"] is False
    assert result["value"] == 2.0


def test_safe_pf_no_trades():
    result = _safe_pf(0.0, 0.0)
    assert result["value"] is None
    assert result["unbounded"] is False


def test_safe_pf_json_serializable():
    parsed = [json.loads(json.dumps(scenario)) for scenario in [_safe_pf(100, 0), _safe_pf(100, 50), _safe_pf(0, 0)]]
    # The finite case must actually be exercised, otherwise the loop below is vacuous.
    assert any(scenario["value"] is not None for scenario in parsed)
    for scenario in parsed:
        assert scenario["value"] is None or math.isfinite(scenario["value"])


class _FakeRound1Db:
    def __init__(self, gross_profit: float, gross_loss: float):
        self._summary = {
            "closed_trades": 3,
            "total_realized": gross_profit - gross_loss,
            "wins": 3 if gross_profit > 0 and gross_loss == 0 else 1,
            "losses": 0 if gross_loss == 0 else 1,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "first_closed_at": "2026-06-22T00:00:00+00:00",
            "last_closed_at": "2026-06-22T00:02:00+00:00",
        }
        self._cohort = {
            "n": 3,
            "wins": self._summary["wins"],
            "gp": gross_profit,
            "gl": gross_loss,
        }

    def execute(self, sql, _params=()):
        if "GROUP BY close_reason" in sql:
            return []
        if "close_reason IN" in sql or "close_reason !=" in sql:
            return _FakeResult(self._cohort)
        return _FakeResult(self._summary)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def test_round1_progress_all_win_profit_factor_is_strict_json(monkeypatch):
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(sqlite, "get_db", lambda: _FakeRound1Db(100.0, 0.0))

    payload = get_round1_progress()
    parsed = json.loads(json.dumps(payload, allow_nan=False))

    assert parsed["profit_factor"] is None
    assert parsed["profit_factor_unbounded"] is True
    for cohort in parsed["cohorts"].values():
        assert cohort["pf"] is None
        assert cohort["pf_unbounded"] is True


def test_round1_progress_mixed_profit_factor_remains_numeric(monkeypatch):
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(sqlite, "get_db", lambda: _FakeRound1Db(100.0, 50.0))

    payload = get_round1_progress()

    assert payload["profit_factor"] == 2.0
    assert payload["profit_factor_unbounded"] is False
    for cohort in payload["cohorts"].values():
        assert cohort["pf"] == 2.0
        assert cohort["pf_unbounded"] is False
