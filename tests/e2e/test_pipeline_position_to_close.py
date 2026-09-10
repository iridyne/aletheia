"""External position → adverse market signal → Operator review and hold."""

import pytest
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import Position
from aletheia.storage import repositories as repo


class TestMarketAwareExitIntegration:
    """Market-aware exit: a counter-signal WAKES the Operator (M5) — it no longer
    auto-tightens the stop or auto-closes."""

    @pytest.mark.asyncio
    async def test_counter_signal_wakes_operator(self, e2e_db, monkeypatch):
        """An opposing Market Observer signal emits an ADVERSE_SIGNAL wake instead of
        mutating the position's stop (M5 — the Operator decides, not the rule)."""
        from aletheia.config.loader import load_config
        cfg = load_config()
        cfg.trading.market_aware_exits.enabled = True
        cfg.exchange.paper_execution_mode = "bitget_demo"

        pos = Position(
            symbol="SOL/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
            status="open",
            opened_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        )
        repo.insert_position(pos)

        # Insert a counter-signal opportunity
        from aletheia.storage.models import Opportunity
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        e2e_db.execute(
            "INSERT INTO universe_registry "
            "(id, venue, market_type, symbol, status) "
            "VALUES ('asset-sol-perp', ?, 'perp', 'SOL/USDT', 'active')",
            ("bitget",),
        )
        e2e_db.execute(
            "INSERT INTO universe_members (id, snapshot_id, asset_id, symbol, rank, total_score, "
            "ttl_sec, expires_at, market_type) "
            "VALUES ('member-sol-perp', 'snap_id', 'asset-sol-perp', 'SOL/USDT', "
            "1, 80, 3600, ?, 'perp')",
            (expires_at,),
        )
        opp = Opportunity(
            symbol="SOL/USDT", market="usdt_futures",
            direction_bias="short", confidence="medium", confidence_score=65,
            source_agent="market_observer",
            universe_member_id="member-sol-perp",
            valid_until=expires_at,
        )
        repo.insert_opportunity(opp)

        # Insert feature snapshot for ATR
        now_iso = datetime.now(timezone.utc).isoformat()
        e2e_db.execute(
            "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct, rsi_14, return_1h, return_4h) "
            "VALUES ('snap1', 'asset-sol-perp', ?, 2.0, 35.0, -1.5, -3.0)",
            (now_iso,),
        )

        from tests.e2e.test_pipeline_exchange_position_to_close import _patch_protective_mark
        _patch_protective_mark(monkeypatch, 99.0)
        monkeypatch.setattr(
            "aletheia.portfolio.position_manager.load_config",
            lambda: cfg,
        )

        # M5: capture wakes — a counter-signal now wakes the Operator, not the stop.
        import aletheia.agent_runtime.pi_daemon as _pd
        wakes = []
        monkeypatch.setattr(
            _pd,
            "notify_pi_daemon",
            lambda source="unknown", event=None: wakes.append(event) or True,
        )

        from aletheia.portfolio.position_manager import evaluate_open_positions
        result = evaluate_open_positions()

        # NOT closed (price 99 > stop 98, score 65 < urgent 80) and NOT auto-tightened —
        # the counter-signal wakes the Operator to decide (M5).
        assert result["closed"] == 0
        assert result["market_signal_wakes"] == 1
        updated = repo.list_positions(status="open")
        sol = [p for p in updated if p["symbol"] == "SOL/USDT"][0]
        assert sol["stop_price"] is None  # no longer auto-tightened

        from aletheia.agent_runtime.wake_events import ADVERSE_SIGNAL
        assert any(getattr(w, "kind", None) == ADVERSE_SIGNAL for w in wakes)

        async def choose_hold(_system, user, *, schema, **_kwargs):
            assert pos.id in user
            assert ADVERSE_SIGNAL in user
            return ({
                "action": "hold",
                "target_position_id": pos.id,
                "reasoning": "counter-signal is not decisive enough to exit",
            }, None)

        monkeypatch.setattr(
            "aletheia.agents.helpers.llm_structured",
            choose_hold,
        )
        from aletheia.agent_shell.commands import review_portfolio_state
        from aletheia.agent_runtime.wake_events import WakeBatch

        review = await review_portfolio_state({
            "trigger": ADVERSE_SIGNAL,
            "wake_batches": [WakeBatch(pos.id, wakes).to_context()],
        })

        decision = repo.get_db().execute(
            "SELECT * FROM pi_decisions WHERE action='hold' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert review["action"] == "hold"
        assert decision is not None
        assert decision["symbol"] == pos.symbol
        assert decision["proposal_reason"] == "position_review"
        assert ADVERSE_SIGNAL in decision["evidence_manifest_json"]
