"""Tests for Edge Lab features: slippage, shadow trades, BTC benchmark, regime tag, family scorecard."""

import json
from unittest.mock import patch, MagicMock
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


class TestSlippage:
    """P0.5: paper_broker applies configurable slippage."""

    def test_apply_slippage_long(self):
        from aletheia.execution.paper_broker import _apply_slippage
        price = 100.0
        result = _apply_slippage(price, "long", 5.0)
        assert result == pytest.approx(100.05, abs=0.001)

    def test_apply_slippage_short(self):
        from aletheia.execution.paper_broker import _apply_slippage
        price = 100.0
        result = _apply_slippage(price, "short", 5.0)
        assert result == pytest.approx(99.95, abs=0.001)

    def test_apply_slippage_zero(self):
        from aletheia.execution.paper_broker import _apply_slippage
        price = 100.0
        result = _apply_slippage(price, "long", 0.0)
        assert result == 100.0

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.execution.paper_broker._btc_price_now", return_value=60000.0)
    @patch("aletheia.execution.paper_broker._current_regime", return_value="risk_on")
    def test_paper_order_uses_slippage(self, _regime, _btc, mock_cfg):
        from aletheia.execution.paper_broker import execute_paper_order

        cfg = MagicMock()
        cfg.trading.slippage_bps = 10.0
        cfg.trading.execution_fee_pct = 0.1
        cfg.trading.default_take_profit_pct = 4.0
        mock_cfg.return_value = cfg

        result = execute_paper_order(
            proposal_id="prop1",
            risk_decision_id="risk1",
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=2000.0,
        )
        # 10 bps slippage on long = 2000 * 1.001 = 2002.0
        assert result["fill_price"] == pytest.approx(2002.0, abs=0.01)

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.execution.paper_broker._btc_price_now", return_value=60000.0)
    @patch("aletheia.execution.paper_broker._current_regime", return_value="risk_off")
    def test_paper_order_records_regime_and_btc(self, _regime, _btc, mock_cfg):
        from aletheia.execution.paper_broker import execute_paper_order
        from aletheia.storage.repositories import list_positions

        cfg = MagicMock()
        cfg.trading.slippage_bps = 0.0
        cfg.trading.execution_fee_pct = 0.1
        cfg.trading.default_take_profit_pct = 4.0
        mock_cfg.return_value = cfg

        execute_paper_order(
            proposal_id="prop2",
            risk_decision_id="risk2",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000.0,
        )

        positions = list_positions(status="open")
        assert len(positions) == 1
        p = positions[0]
        assert p["btc_entry_price"] == 60000.0
        assert p["regime_tag"] == "risk_off"


class TestShadowTrades:
    """P1: shadow trade persistence."""

    def test_persist_shadow_batch(self):
        from aletheia.analytics.shadow_trades import persist_shadow_batch
        from aletheia.storage.repositories import list_shadow_trades

        candidates = [
            {"opportunity_id": "opp1", "symbol": "BTC/USDT", "direction": "long",
             "scanner_family": "momentum", "trade_score": 80.0},
            {"opportunity_id": "opp2", "symbol": "ETH/USDT", "direction": "short",
             "scanner_family": "mean_reversion", "trade_score": 60.0},
            {"opportunity_id": "opp3", "symbol": "SOL/USDT", "direction": "long",
             "scanner_family": "derivatives", "trade_score": 40.0},
        ]
        selected = candidates[0]

        with patch("aletheia.analytics.shadow_trades._current_price", return_value=50000.0):
            ids = persist_shadow_batch(candidates, selected, top_n=3)

        assert len(ids) == 3
        shadows = list_shadow_trades()
        assert len(shadows) == 3
        # First one should be marked as selected
        selected_shadows = [s for s in shadows if s["was_selected"]]
        assert len(selected_shadows) == 1
        assert selected_shadows[0]["opportunity_id"] == "opp1"
        # All should share the same batch_id
        batch_ids = {s["batch_id"] for s in shadows}
        assert len(batch_ids) == 1

    def test_persist_empty_candidates(self):
        from aletheia.analytics.shadow_trades import persist_shadow_batch
        ids = persist_shadow_batch([], None, top_n=5)
        assert ids == []


class TestBTCBenchmark:
    """P1.5: BTC price recorded on position close."""

    @patch("aletheia.portfolio.pnl._btc_price_at_close", return_value=61000.0)
    def test_btc_exit_price_on_close(self, _mock_btc):
        from aletheia.storage.repositories import insert_position, _select_one
        from aletheia.storage.models import Position
        from aletheia.portfolio.pnl import close_position

        pos = Position(
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=2000.0,
            current_price=2000.0,
            execution_mode="paper_internal",
            btc_entry_price=60000.0,
        )
        insert_position(pos)

        with patch("aletheia.files.jsonl.write_audit"):
            close_position(pos.id, exit_price=2100.0, reason="take_profit")

        row = _select_one("positions", pos.id)
        assert row["btc_exit_price"] == 61000.0
        assert row["status"] == "closed"


class TestFamilyScorecard:
    """P2.5: family scorecard aggregation."""

    def test_empty_scorecard(self):
        from aletheia.analytics.family_scorecard import compute_family_scorecard
        result = compute_family_scorecard()
        assert result == []

    def test_scorecard_with_data(self):
        from aletheia.storage.sqlite import get_db
        from aletheia.analytics.family_scorecard import compute_family_scorecard

        db = get_db()
        # Insert a closed position with linked proposal/opportunity
        db.execute(
            "INSERT INTO opportunities (id, symbol, market, scanner_family, status) "
            "VALUES ('opp1', 'BTC/USDT', 'spot', 'momentum', 'closed')"
        )
        db.execute(
            "INSERT INTO trade_proposals (id, opportunity_id, symbol, market_type, direction) "
            "VALUES ('tp1', 'opp1', 'BTC/USDT', 'spot', 'long')"
        )
        db.execute(
            "INSERT INTO positions (id, symbol, market_type, direction, quantity, "
            "entry_price, realized_pnl, status, proposal_id, execution_mode, "
            "btc_entry_price, btc_exit_price, opened_at, closed_at, close_category, "
            "exit_price_source, exit_fill_verified, outcome_eligible, "
            "outcome_exclusion_reason, outcome_semantics_version) "
            "VALUES ('pos1', 'BTC/USDT', 'spot', 'long', 0.01, 60000.0, 50.0, "
            "'closed', 'tp1', 'paper_internal', 60000.0, 61000.0, "
            "'2026-07-12T07:30:00+00:00', '2026-07-12T08:00:00+00:00', 'strategy', "
            "'local_fill', 1, 1, 'eligible', 'oi-v1')"
        )
        db.commit()

        result = compute_family_scorecard()
        assert len(result) == 1
        assert result[0]["family"] == "momentum"
        assert result[0]["trade_count"] == 1
        assert result[0]["win_rate"] == 100.0
        assert result[0]["alpha_vs_btc_pct"] is not None


class TestOpportunityExtendedFields:
    """P0: Opportunity model has valid_until, entry_zone, stop_loss_hint, tp_hint."""

    def test_opportunity_with_edge_lab_fields(self):
        from aletheia.storage.models import Opportunity
        from aletheia.storage.repositories import insert_opportunity, _select_one

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
