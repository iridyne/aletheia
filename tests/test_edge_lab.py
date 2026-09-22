"""Tests for Edge Lab features: shadow trades, BTC benchmark and family scorecard."""

import json
from unittest.mock import patch

import pytest

@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Use a fresh file-backed DB for each test."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn

    from aletheia.storage.migrations import run_migrations
    run_migrations()
    yield conn
    conn.close()
    sq._db_conn = None


class TestShadowTrades:
    """P1: shadow trade persistence."""

    def test_persist_shadow_batch(self):
        from extensions.counterfactual.trades import persist_shadow_batch
        from aletheia.storage.repositories import list_shadow_trades

        candidates = [
            {"opportunity_id": "opp1", "symbol": "BTC/USDT", "direction": "long",
             "scanner_family": "momentum", "trade_score": 80.0,
             "learning_score_trace": [
                 {"path_id": "ranker_case_score", "proposed_delta": -4.0}
             ]},
            {"opportunity_id": "opp2", "symbol": "ETH/USDT", "direction": "short",
             "scanner_family": "mean_reversion", "trade_score": 60.0},
            {"opportunity_id": "opp3", "symbol": "SOL/USDT", "direction": "long",
             "scanner_family": "derivatives", "trade_score": 40.0},
        ]
        selected = candidates[0]

        with patch("extensions.counterfactual.trades._current_price", return_value=50000.0):
            ids = persist_shadow_batch(candidates, selected, top_n=3)

        assert len(ids) == 3
        shadows = list_shadow_trades()
        assert len(shadows) == 3
        # First one should be marked as selected
        selected_shadows = [s for s in shadows if s["was_selected"]]
        assert len(selected_shadows) == 1
        assert selected_shadows[0]["opportunity_id"] == "opp1"
        assert json.loads(selected_shadows[0]["learning_score_trace_json"]) == [
            {"path_id": "ranker_case_score", "proposed_delta": -4.0}
        ]
        # All should share the same batch_id
        batch_ids = {s["batch_id"] for s in shadows}
        assert len(batch_ids) == 1

    def test_persist_empty_candidates(self):
        from extensions.counterfactual.trades import persist_shadow_batch
        ids = persist_shadow_batch([], None, top_n=5)
        assert ids == []


class TestBTCBenchmark:
    """P1.5: BTC price recorded on position close."""

    @patch("aletheia.portfolio.pnl._btc_price_at_close", return_value=61000.0)
    def test_btc_exit_price_on_close(self, _mock_btc):
        from tests.test_outcome_closure import _insert_closed_position_via_producer

        row = _insert_closed_position_via_producer(
            position_id="benchmark-close", symbol="ETH/USDT", market_type="spot",
            direction="long", quantity=1.0, entry_price=2000.0, realized_pnl=100.0,
            execution_mode="bitget_demo", closed_at="2026-07-12T08:00:00+00:00",
            close_reason="take_profit", btc_entry_price=60000.0,
        )

        assert row["btc_exit_price"] == 61000.0
        assert row["status"] == "closed"


class TestFamilyScorecard:
    """Family scorecard aggregation from verified exchange outcomes."""

    def test_empty_scorecard(self, optional_evidence_jobs):
        from aletheia.analytics.family_scorecard import compute_family_scorecard
        result = compute_family_scorecard()
        assert result == []

    @patch("aletheia.portfolio.pnl._btc_price_at_close", return_value=65000.0)
    def test_scorecard_with_data(self, _mock_btc, optional_evidence_jobs):
        from aletheia.analytics.family_scorecard import compute_family_scorecard
        from tests.test_outcome_closure import _insert_closed_position_via_producer

        _insert_closed_position_via_producer(
            position_id="scorecard-close", symbol="BTC/USDT", market_type="spot",
            direction="long", quantity=0.01, entry_price=60000.0, realized_pnl=50.0,
            execution_mode="bitget_demo", opened_at="2026-07-12T07:30:00+00:00",
            closed_at="2026-07-12T08:00:00+00:00", close_reason="take_profit",
            scanner_family="momentum", btc_entry_price=60000.0,
        )

        result = compute_family_scorecard()
        assert len(result) == 1
        assert result[0]["family"] == "momentum"
        assert result[0]["trade_count"] == 1
        assert result[0]["win_rate"] == 100.0
        assert result[0]["alpha_vs_btc_pct"] == pytest.approx(0.0, abs=0.01)


class TestOpportunityExtendedFields:
    """P0: Opportunity model has valid_until, entry_zone, stop_loss_hint, tp_hint."""

    def test_opportunity_with_edge_lab_fields(self):
        from aletheia.storage.models import Opportunity
        from aletheia.storage.repositories import _select_one, insert_opportunity

        opp = Opportunity(
            symbol="ETH/USDT",
            market="spot",
            direction_bias="long",
            valid_until="2026-06-14T15:00:00+00:00",
            entry_zone='{"low": 2000, "high": 2050}',
            stop_loss_hint=1950.0,
            tp_hint=2200.0,
        )
        insert_opportunity(opp)
        row = _select_one("opportunities", opp.id)
        assert row["valid_until"] == "2026-06-14T15:00:00+00:00"
        assert row["stop_loss_hint"] == 1950.0
        assert row["tp_hint"] == 2200.0
        assert "2000" in row["entry_zone"]
