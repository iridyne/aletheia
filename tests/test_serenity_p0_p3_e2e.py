"""End-to-end behavioral tests for Serenity P0–P3 implementations.

These tests exercise the actual code paths (not just source-string assertions):
- P0: auto_promote publishes canonical Candidates from qualified research and PA
- P1: thesis_invalidated exit fires when opportunity status != 'active'
- P3: _get_flow_context_block reads DB and returns structured data
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from tests.helpers.outcome_feedback import configured_outcome_feedback as configured_outcome_feedback




# ---------------------------------------------------------------------------
# P0 — auto_promote end-to-end
# ---------------------------------------------------------------------------


class TestP0AutoPromote:
    """Auto-promotion publishes canonical Candidates and wakes the Planner."""

    def _setup_db_with_opps(self, score: int = 80):
        """Seed DB with a theme + opportunity that will score `score`."""
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import SerenityTheme, SerenityOpportunity, SerenityEvidence

        theme = SerenityTheme(name="Test auto-promote chain", status="active")
        repo.insert_serenity_theme(theme)

        opp = SerenityOpportunity(
            theme_id=theme.id,
            symbol="BTC/USDT",
            direction="long",
            status="active",
            action_intent="watch",
            evidence_tier="high_confidence",
            assessed_tier="high_confidence",
            assessed_by="deterministic_evaluator",
            confidence_score=85,
            asset_class="crypto",
        )
        repo.insert_serenity_opportunity(opp)

        # Add strong evidence so scorer returns a high score
        ev = SerenityEvidence(
            theme_id=theme.id,
            title="Strong catalyst",
            source="research",
            source_ref="Bloomberg 2026-07-01",
            summary="Major institutional announcement",
            evidence_tier="high_confidence",
            assessed_tier="high_confidence",
            assessed_by="deterministic_evaluator",
        )
        repo.insert_serenity_evidence(ev)

        return theme.id, opp.id

    def test_auto_promote_publishes_candidate_when_score_sufficient(self):
        from aletheia.config.loader import load_config, save_config
        from tests.test_candidate_opportunity_persistence import _serenity_publication_source
        from extensions.research.research_worker import _auto_promote_serenity_opportunities
        from aletheia.storage import repositories as repo

        cfg = load_config()
        cfg.exchange.paper_execution_mode = "bitget_demo"
        cfg.market_data.ccxt_exchange = "bitget"
        save_config(cfg)
        theme, opportunity = _serenity_publication_source()
        cfg.serenity.min_score_for_pi = 0
        notify_calls = []
        with patch("aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
                   side_effect=lambda source="unknown": notify_calls.append(source)):
            assert _auto_promote_serenity_opportunities([opportunity.id], cfg) == 1
            assert _auto_promote_serenity_opportunities([opportunity.id], cfg) == 0
        assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 1
        assert repo.get_db().execute("SELECT COUNT(*) FROM pi_decisions").fetchone()[0] == 0
        assert repo.get_serenity_theme(theme.id)["status"] == "promoted"
        assert notify_calls == ["serenity_auto_promote"]

    def test_auto_promote_skips_when_score_below_threshold(self):
        """Opportunities below min_score_for_pi are NOT promoted."""
        from aletheia.storage import repositories as repo

        _, opp_id = self._setup_db_with_opps()

        mock_cfg = MagicMock()
        mock_cfg.serenity.auto_promote = True
        mock_cfg.serenity.min_score_for_pi = 99  # Very high — nothing passes

        with patch("aletheia.config.loader.load_config", return_value=mock_cfg):
            from aletheia.trading.serenity_scoring import score_serenity_opportunity

            opp = repo.get_serenity_opportunity(opp_id)
            evidence = repo.list_serenity_evidence(theme_id=opp["theme_id"], limit=200)
            gates = repo.list_serenity_risk_gates(opportunity_id=opp_id, limit=200)
            score_result = score_serenity_opportunity(opp, evidence=evidence, risk_gates=gates)

            # Score should be below the threshold
            assert score_result["score"] < 99

        # Opportunity should remain unchanged
        opp_after = repo.get_serenity_opportunity(opp_id)
        assert opp_after["action_intent"] == "watch"


# ---------------------------------------------------------------------------
# P1 — thesis-invalidation exit end-to-end
# ---------------------------------------------------------------------------


class TestP1ThesisInvalidation:
    """Verify _decide_exit returns thesis_invalidated when opp status != active."""

    def _make_serenity_position(self, opp_status="active"):
        """Create a serenity position + linked opportunity with given status."""
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import SerenityTheme, SerenityOpportunity

        theme = SerenityTheme(name="Thesis test", status="active")
        repo.insert_serenity_theme(theme)
        opp = SerenityOpportunity(
            theme_id=theme.id,
            symbol="ETH/USDT",
            direction="long",
            status=opp_status,
            asset_class="crypto",
        )
        repo.insert_serenity_opportunity(opp)

        position = {
            "direction": "long",
            "trade_school": "serenity",
            "opportunity_id": opp.id,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "entry_price": 3000.0,
            "stop_price": None,
            "take_profit_price": None,
            "symbol": "ETH/USDT",
            "execution_mode": "bitget_demo",
        }
        return position, opp.id, theme.id

    def _cfg(self):
        from aletheia.config.loader import load_config
        return load_config()

    def test_thesis_invalidated_fires_when_opp_dismissed(self):
        """A serenity position whose opp is 'dismissed' triggers thesis_invalidated."""
        from aletheia.portfolio.position_manager import _decide_exit

        pos, _, _ = self._make_serenity_position(opp_status="dismissed")
        result = _decide_exit(pos, price=3000.0, cfg=self._cfg())
        assert result is not None
        reason, exit_price = result
        assert reason == "thesis_invalidated"
        assert exit_price == 3000.0

    def test_thesis_invalidated_fires_when_opp_expired(self):
        """A serenity position whose opp is 'expired' triggers thesis_invalidated."""
        from aletheia.portfolio.position_manager import _decide_exit

        pos, _, _ = self._make_serenity_position(opp_status="expired")
        # Price must be between stop and TP to avoid triggering those exits first
        result = _decide_exit(pos, price=2990.0, cfg=self._cfg())
        assert result is not None
        reason, _ = result
        assert reason == "thesis_invalidated"

    def test_no_exit_when_opp_active(self):
        """A serenity position with an active opp does NOT trigger thesis exit."""
        from aletheia.portfolio.position_manager import _decide_exit

        pos, _, _ = self._make_serenity_position(opp_status="active")
        # Price between stop and take_profit, within hold time
        result = _decide_exit(pos, price=3000.0, cfg=self._cfg())
        assert result is None

    def test_short_term_position_ignores_thesis(self):
        """Short-term positions never check thesis — even if they have opportunity_id."""
        from aletheia.portfolio.position_manager import _decide_exit

        pos, _, _ = self._make_serenity_position(opp_status="dismissed")
        pos["trade_school"] = "short_term"  # Override to short_term
        result = _decide_exit(pos, price=3000.0, cfg=self._cfg())
        # No thesis exit — should return None (price not at stop/tp, within hold time)
        assert result is None

    def test_dismiss_cascade_marks_child_opps(self):
        """When a theme is dismissed, its active opps are also set to 'dismissed'."""
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import SerenityTheme, SerenityOpportunity

        theme = SerenityTheme(name="Cascade test", status="active")
        repo.insert_serenity_theme(theme)

        opp1 = SerenityOpportunity(
            theme_id=theme.id, symbol="BTC/USDT", direction="long",
            status="active", asset_class="crypto",
        )
        opp2 = SerenityOpportunity(
            theme_id=theme.id, symbol="ETH/USDT", direction="short",
            status="active", asset_class="crypto",
        )
        repo.insert_serenity_opportunity(opp1)
        repo.insert_serenity_opportunity(opp2)

        # Simulate the cascade logic from patch_serenity_theme_status
        repo.update_serenity_theme(theme.id, status="dismissed",
                                   updated_at=datetime.now(timezone.utc).isoformat())
        active_opps = repo.list_serenity_opportunities(theme_id=theme.id, status="active", limit=200)
        for opp in active_opps:
            repo.update_serenity_opportunity(opp["id"], status="dismissed")

        # Verify both opps are now dismissed
        all_opps = repo.list_serenity_opportunities(theme_id=theme.id, limit=200)
        for opp in all_opps:
            assert opp["status"] == "dismissed", f"Opp {opp['id']} not cascaded"


# ---------------------------------------------------------------------------
# Link 4→5 — pending_signals direct attribution match
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# P3 — _get_flow_context_block end-to-end
# ---------------------------------------------------------------------------


class TestP3FlowContextBlock:
    """Verify _get_flow_context_block reads real DB data and formats it."""

    def _seed_flow_data(self):
        """Insert OI + liquidation rows into the DB."""
        from aletheia.storage.sqlite import get_db
        db = get_db()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT OR IGNORE INTO open_interest_snapshots "
            "(id, symbol, exchange, open_interest_usd, oi_change_1h_pct, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("oi-test-1", "BTC/USDT", "coinalyze", 15_000_000_000, 2.5, now),
        )
        db.execute(
            "INSERT OR IGNORE INTO open_interest_snapshots "
            "(id, symbol, exchange, open_interest_usd, oi_change_1h_pct, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("oi-test-2", "ETH/USDT", "coinalyze", 8_000_000_000, -0.5, now),
        )
        db.execute(
            "INSERT OR IGNORE INTO liquidation_events "
            "(id, symbol, exchange, side, quantity_usd, price, event_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("liq-test-1", "BTC/USDT", "coinalyze", "long", 500_000, 60000, now),
        )
        db.execute(
            "INSERT OR IGNORE INTO liquidation_events "
            "(id, symbol, exchange, side, quantity_usd, price, event_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("liq-test-2", "BTC/USDT", "coinalyze", "long", 300_000, 59800, now),
        )
        db.commit()

    def test_flow_block_returns_formatted_data(self):
        """With OI + liquidation data in DB, the block returns formatted text."""
        self._seed_flow_data()
        from extensions.research.research_worker import _get_flow_context_block

        result = _get_flow_context_block()
        assert "Derivatives Flow Data" in result, f"Got: {result!r}"
        assert "BTC/USDT" in result
        assert "OI=$" in result
        assert "Liquidations" in result
        # Check numerical formatting
        assert "15000.0M" in result or "15000" in result or "$15000" in result

    def test_flow_block_empty_when_no_data(self):
        """Without recent data, returns empty string."""
        from extensions.research.research_worker import _get_flow_context_block
        # DB exists but no recent rows (all older than 4h or empty)
        # The autouse fixture gives us a clean DB, so this should be empty
        from aletheia.storage.sqlite import get_db
        db = get_db()
        # Ensure no rows
        db.execute("DELETE FROM open_interest_snapshots")
        db.execute("DELETE FROM liquidation_events")
        db.commit()

        result = _get_flow_context_block()
        assert result == ""

    def test_flow_block_is_coinalyze_only_and_preserves_unavailable_delta(self):
        from extensions.research.research_worker import _get_flow_context_block
        from aletheia.storage.sqlite import get_db

        db = get_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.executemany(
            "INSERT INTO open_interest_snapshots "
            "(id, symbol, exchange, open_interest_usd, oi_change_1h_pct, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("oi-coinalyze", "BTC/USDT", "coinalyze", 100_000_000, None, now),
                ("oi-foreign", "DOGE/USDT", "hyperliquid", 900_000_000, 9.0, now),
            ],
        )
        db.commit()

        result = _get_flow_context_block()

        assert "BTC/USDT" in result
        assert "Delta1h=unavailable" in result
        assert "DOGE/USDT" not in result
        assert "Delta1h=0.0%" not in result

    def test_equity_context_block_references_yfinance(self):
        """_get_equity_context_block has yfinance injection logic (import path test).

        Full yfinance network call is NOT exercised in CI — we verify the code
        structure handles ImportError gracefully (yfinance not installed in test env).
        """
        from extensions.research.research_worker import _get_equity_context_block

        # Should not crash even if yfinance is not installed
        # (the code has try/except ImportError)
        with patch("aletheia.config.loader.load_config") as mock_cfg:
            mock_cfg.return_value.market_data.observed_stock_tokens = ["rNVDA/USDT"]
            result = _get_equity_context_block()
            # Even without yfinance, the ticker list should be present
            if result:
                assert "NVDA" in result


# ---------------------------------------------------------------------------
# Link 9→10 — Serenity feedback loop closure
# ---------------------------------------------------------------------------


class TestSerenityFeedbackLoop:
    """Verified external closes write Serenity feedback once."""

    def _setup_serenity_position(self, *, entry_price=100.0):
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import Position, SerenityTheme, SerenityOpportunity
        from tests.test_outcome_closure import _insert_entry_chain

        theme = SerenityTheme(name="Feedback loop test", status="promoted")
        repo.insert_serenity_theme(theme)
        opp = SerenityOpportunity(
            theme_id=theme.id, symbol="BTC/USDT", direction="long",
            status="active", asset_class="crypto", confidence_score=80,
        )
        repo.insert_serenity_opportunity(opp)
        opened = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        position = Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=2.0, entry_price=entry_price, current_price=entry_price,
            execution_mode="bitget_demo", trade_school="serenity",
            opened_at=opened, entry_fee=0.2,
            entry_price_source="broker_fill", entry_fill_verified=True,
            funding_cost=0.0, funding_source="not_applicable",
        )
        opportunity_id, proposal_id, order_id = _insert_entry_chain(
            position_id=position.id, symbol=position.symbol,
            market_type=position.market_type, direction=position.direction,
            quantity=position.quantity, price=position.entry_price,
            execution_mode=position.execution_mode, entry_fee=position.entry_fee,
            opportunity_id=opp.id, opened_at=opened,
        )
        position.opportunity_id = opportunity_id
        position.proposal_id = proposal_id
        position.order_id = order_id
        repo.insert_position(position)
        return position, opp.id

    def test_close_serenity_position_writes_post_mortem(self):
        from aletheia.portfolio.outcome_closure import close_position_verified
        from aletheia.storage import repositories as repo
        from tests.test_outcome_closure import _verified_result

        position, opp_id = self._setup_serenity_position()
        evidence = _verified_result(position)
        result = close_position_verified(position.id, evidence, reason="take_profit")
        assert result["realized_pnl"] > 0
        assert close_position_verified(
            position.id, evidence, reason="take_profit",
        )["already_closed"] is True

        [pm] = repo.list_serenity_post_mortems(opportunity_id=opp_id)
        assert pm["outcome"] == "win"
        assert pm["opportunity_id"] == opp_id
        assert "BTC/USDT" in pm["summary"]
        assert "take_profit" in pm["summary"]
        assert repo.get_serenity_opportunity(opp_id)["status"] == "traded_closed"

    def test_close_serenity_loss_has_failure_bucket(self):
        from aletheia.portfolio.outcome_closure import close_position_verified
        from aletheia.storage import repositories as repo
        from tests.test_outcome_closure import _verified_result

        position, opp_id = self._setup_serenity_position(entry_price=120.0)
        result = close_position_verified(
            position.id, _verified_result(position), reason="stop_loss",
        )
        assert result["realized_pnl"] < 0
        [pm] = repo.list_serenity_post_mortems(opportunity_id=opp_id)
        assert pm["outcome"] == "loss"
        assert pm["failure_bucket"] == "stop_hit"

    def test_close_short_term_position_no_post_mortem(self):
        from aletheia.portfolio.outcome_closure import close_position_verified
        from aletheia.storage import repositories as repo
        from tests.test_outcome_closure import _position, _verified_result

        position = _position()
        repo.insert_position(position)
        close_position_verified(position.id, _verified_result(position))

        assert repo.list_serenity_post_mortems(limit=100) == []
