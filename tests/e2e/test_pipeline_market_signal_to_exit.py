"""Pipeline 5: Market Observer signal → position_manager → exit/tighten.

Verifies that Market Observer's opportunity output is correctly consumed
by the position manager's market-aware exit rules. Tests the SEAM between
the observer (writer) and position_manager (reader).
"""

import pytest
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import Opportunity, Position
from aletheia.storage import repositories as repo
from aletheia.portfolio.market_signal_reader import get_market_signals_for_positions


class TestSignalReaderConsumeObserverOutput:
    """market_signal_reader correctly reads Market Observer's opportunity rows."""

    def test_reads_direction_and_score(self, e2e_db):
        """Field names align: observer writes direction_bias, reader reads it."""
        opp = Opportunity(
            symbol="SOL/USDT", market="spot",
            direction_bias="short", confidence="medium", confidence_score=65,
            source_agent="market_observer",
        )
        repo.insert_opportunity(opp)

        signals = get_market_signals_for_positions(["SOL/USDT"])
        assert "SOL/USDT" in signals
        ctx = signals["SOL/USDT"]
        assert ctx.direction_bias == "short"
        assert ctx.confidence_score == 65
        assert ctx.signal_age_min is not None
        assert ctx.signal_age_min < 1.0

    def test_ignores_non_crypto_opportunities(self, e2e_db):
        """Only asset_class='crypto' opportunities are read."""
        opp = Opportunity(
            symbol="AAPL", market="equity",
            direction_bias="long", confidence="high", confidence_score=80,
            source_agent="equity_observer", asset_class="equity",
        )
        repo.insert_opportunity(opp)

        signals = get_market_signals_for_positions(["AAPL"])
        ctx = signals.get("AAPL")
        assert ctx is not None
        assert ctx.direction_bias is None  # not picked up (equity filtered out)

    def test_picks_most_recent_per_symbol(self, e2e_db):
        """When multiple opportunities exist, uses the newest."""
        old = Opportunity(
            symbol="ETH/USDT", market="spot",
            direction_bias="long", confidence="medium", confidence_score=55,
            source_agent="market_observer",
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        )
        new = Opportunity(
            symbol="ETH/USDT", market="spot",
            direction_bias="short", confidence="high", confidence_score=78,
            source_agent="market_observer",
        )
        repo.insert_opportunity(old)
        repo.insert_opportunity(new)

        signals = get_market_signals_for_positions(["ETH/USDT"])
        ctx = signals["ETH/USDT"]
        assert ctx.direction_bias == "short"
        assert ctx.confidence_score == 78

    def test_features_augment_via_universe_join(self, e2e_db):
        """Feature snapshots are joined via universe_members.asset_id."""
        now = datetime.now(timezone.utc)
        repo.insert_opportunity(Opportunity(
            symbol="BTC/USDT", market="spot",
            direction_bias="long", confidence_score=70,
            source_agent="market_observer",
        ))
        # Insert member + feature snapshot (all NOT NULL fields filled)
        expires = (now + timedelta(hours=1)).isoformat()
        e2e_db.execute(
            "INSERT INTO universe_members (id, snapshot_id, asset_id, symbol, rank, total_score, "
            "ttl_sec, expires_at, market_type) "
            "VALUES ('m1', 's1', 'asset_btc', 'BTC/USDT', 1, 90, 3600, ?, 'crypto')",
            (expires,),
        )
        e2e_db.execute(
            "INSERT INTO universe_feature_snapshots (id, asset_id, ts, rsi_14, return_1h, return_4h, atr_pct) "
            "VALUES ('f1', 'asset_btc', ?, 45.0, -0.5, 2.1, 1.5)",
            (now.isoformat(),),
        )

        signals = get_market_signals_for_positions(["BTC/USDT"])
        ctx = signals["BTC/USDT"]
        assert ctx.rsi_14 == 45.0
        assert ctx.atr_pct == 1.5
        assert ctx.return_1h == -0.5


class TestExitRulesConsumeSignal:
    """exit_rules correctly interprets the MarketSignalContext."""

    def test_counter_signal_detected(self, e2e_db):
        """Long position + short signal = counter detected → action."""
        from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
        from aletheia.portfolio.market_signal_reader import MarketSignalContext

        pos = {
            "id": "p1", "symbol": "SOL/USDT", "direction": "long",
            "entry_price": 100.0, "stop_price": None,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "execution_mode": "paper_internal",
        }
        signal = MarketSignalContext(
            symbol="SOL/USDT", direction_bias="short",
            confidence_score=65, signal_age_min=5.0, atr_pct=2.0,
        )

        class Cfg:
            class trading:
                class market_aware_exits:
                    enabled = True
                    min_hold_hours_before_market_exit = 0.5
                    max_signal_age_min = 35.0
                    urgent_exit_score = 80
                    urgent_exit_max_pnl_pct = -0.3
                    tighten_stop_score = 60
                    confidence_collapse_score = 30
                    tighten_atr_mult = 0.8
                    tighten_pct = 0.8

        result = evaluate_market_exit_rules(pos, 98.0, signal, Cfg())
        assert result.action == "tighten_stop"
        assert result.new_stop_price is not None
        # 98 - (98 * 0.02 * 0.8) = 98 - 1.568 = 96.432
        assert abs(result.new_stop_price - 96.432) < 0.01

    def test_confirming_signal_no_action(self, e2e_db):
        """Long position + long signal = confirming → no action."""
        from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
        from aletheia.portfolio.market_signal_reader import MarketSignalContext

        pos = {
            "id": "p2", "symbol": "BTC/USDT", "direction": "long",
            "entry_price": 60000.0, "stop_price": None,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
            "execution_mode": "paper_internal",
        }
        signal = MarketSignalContext(
            symbol="BTC/USDT", direction_bias="long",
            confidence_score=75, signal_age_min=3.0, atr_pct=1.5,
        )

        class Cfg:
            class trading:
                class market_aware_exits:
                    enabled = True
                    min_hold_hours_before_market_exit = 0.5
                    max_signal_age_min = 35.0
                    urgent_exit_score = 80
                    urgent_exit_max_pnl_pct = -0.3
                    tighten_stop_score = 60
                    confidence_collapse_score = 30
                    tighten_atr_mult = 0.8
                    tighten_pct = 0.8

        result = evaluate_market_exit_rules(pos, 61000.0, signal, Cfg())
        assert result.action == "none"
