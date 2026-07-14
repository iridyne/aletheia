"""Pipeline 3: position 开仓 → price feed → position_manager → 平仓.

Verifies that an open position with stop_loss set correctly triggers close
when price breaches it, and that the close writes realized_pnl correctly.
"""

import pytest
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import Position
from aletheia.storage import repositories as repo
from aletheia.portfolio.pnl import close_position


class TestStopLossCloses:
    """Position is closed when price breaches stop_loss."""

    def test_long_stop_loss_triggers(self, e2e_db, monkeypatch):
        """Long position closed when price drops below stop_price."""
        pos = Position(
            symbol="SOL/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
            status="open",
            stop_price=98.0,
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        repo.insert_position(pos)

        # Mock price feed to return price below stop
        monkeypatch.setattr(
            "aletheia.portfolio.position_manager._latest_price",
            lambda sym: 97.5,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        assert result["closed"] == 1
        closed = repo.list_positions(status="closed")
        assert len(closed) == 1
        assert closed[0]["close_reason"] == "stop_loss"
        assert closed[0]["realized_pnl"] < 0

    def test_short_stop_loss_triggers(self, e2e_db, monkeypatch):
        """Short position closed when price rises above stop_price."""
        pos = Position(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="short",
            quantity=0.1,
            entry_price=2000.0,
            execution_mode="paper_internal",
            status="open",
            stop_price=2040.0,
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        repo.insert_position(pos)

        monkeypatch.setattr(
            "aletheia.portfolio.position_manager._latest_price",
            lambda sym: 2050.0,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        assert result["closed"] == 1
        closed = repo.list_positions(status="closed")
        assert closed[0]["close_reason"] == "stop_loss"


class TestTakeProfitCloses:
    """Position is closed when price reaches take_profit."""

    def test_long_take_profit(self, e2e_db, monkeypatch):
        pos = Position(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            entry_price=60000.0,
            execution_mode="paper_internal",
            status="open",
            take_profit_price=62400.0,
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        repo.insert_position(pos)

        monkeypatch.setattr(
            "aletheia.portfolio.position_manager._latest_price",
            lambda sym: 62500.0,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        assert result["closed"] == 1
        closed = repo.list_positions(status="closed")
        assert closed[0]["close_reason"] == "take_profit"
        assert closed[0]["realized_pnl"] > 0


class TestMaxHoldCloses:
    """Position is force-closed after max_position_hold_hours."""

    def test_max_hold_triggers(self, e2e_db, monkeypatch):
        pos = Position(
            symbol="SOL/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=70.0,
            execution_mode="paper_internal",
            status="open",
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat(),
        )
        repo.insert_position(pos)

        monkeypatch.setattr(
            "aletheia.portfolio.position_manager._latest_price",
            lambda sym: 71.0,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        assert result["closed"] == 1
        closed = repo.list_positions(status="closed")
        assert closed[0]["close_reason"] == "max_hold"


class TestMarketAwareExitIntegration:
    """Market-aware exit rules tighten stop when counter-signal detected."""

    def test_counter_signal_tightens_stop(self, e2e_db, monkeypatch):
        """Opposing Market Observer signal tightens stop_price on next cycle."""
        from aletheia.config.loader import load_config
        cfg = load_config()
        cfg.trading.market_aware_exits.enabled = True

        pos = Position(
            symbol="SOL/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
            status="open",
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        )
        repo.insert_position(pos)

        # Insert a counter-signal opportunity
        from aletheia.storage.models import Opportunity
        opp = Opportunity(
            symbol="SOL/USDT", market="spot",
            direction_bias="short", confidence="medium", confidence_score=65,
            source_agent="market_observer",
        )
        repo.insert_opportunity(opp)

        # Insert feature snapshot for ATR
        now_iso = datetime.now(timezone.utc).isoformat()
        e2e_db.execute(
            "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct, rsi_14, return_1h, return_4h) "
            "VALUES ('snap1', 'member1', ?, 2.0, 35.0, -1.5, -3.0)",
            (now_iso,),
        )
        e2e_db.execute(
            "INSERT INTO universe_members (id, snapshot_id, asset_id, symbol, rank, total_score, "
            "ttl_sec, expires_at, market_type) "
            "VALUES ('member_row', 'snap_id', 'member1', 'SOL/USDT', 1, 80, 3600, ?, 'crypto')",
            ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),),
        )

        # Price at 99.0: only -1% loss, within stop (default 2% = stop at 98.0)
        monkeypatch.setattr(
            "aletheia.portfolio.position_manager._latest_price",
            lambda sym: 99.0,
        )
        monkeypatch.setattr(
            "aletheia.portfolio.position_manager.load_config",
            lambda: cfg,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        # Position should NOT be closed (price 99 > stop 98, score=65 < urgent=80)
        assert result["closed"] == 0
        # But stop_price should be tightened
        updated = repo.list_positions(status="open")
        sol = [p for p in updated if p["symbol"] == "SOL/USDT"][0]
        assert sol["stop_price"] is not None
        # new_stop = 99 - (99 * 0.02 * 0.8) = 99 - 1.584 = 97.416
        assert sol["stop_price"] > 97.0
