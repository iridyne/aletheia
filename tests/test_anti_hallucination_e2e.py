"""E2E: anti-hallucination attribution chain (R0) — opportunity → context → decision.

Locks the R0 fix end-to-end (LEARNING_LOOP_AUDIT_2026-06-26 §R0): a seeded open
opportunity must surface into the System Operator's Layer-1 context WITH its id +
scanner_family, and when the PI picks that symbol+direction,
_attach_opportunity_context backfills the real opportunity_id (+ strategy_id +
scanner_family) onto the decision — instead of the old hard-coded None that severed
the whole learning attribution chain (trade_case_study entry-context / guard
predicates / reasoning_feedback scanner all went 'unknown').

Behavioral (DB-backed real functions, no mock-call-verify) — complements the
structural AST guard in spec_learning_pipeline::LP-S05.
"""
import pytest

from tests.test_entry_logic import setup_env
































def test_data_health_signal_win_rate_excludes_unknown_direction(tmp_path, monkeypatch):
    """System Operator data health must not count unknown-direction outcomes as long.

    Historical NULL direction rows are not honest long or short evidence. They
    should not affect the signal win-rate that the operator sees in Layer 1.
    """
    setup_env(tmp_path, monkeypatch)

    from datetime import datetime, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome

    now = datetime.now(timezone.utc).isoformat()

    def _outcome(symbol: str, direction: str | None, ret: float) -> UniverseForwardOutcome:
        directional = ret if direction == "long" else -ret if direction == "short" else None
        return UniverseForwardOutcome(
            member_id=f"m_{symbol}_{direction}_{ret}",
            snapshot_id="snap_data_health",
            symbol=symbol,
            venue="bitget",
            scanner_family="momentum",
            direction=direction,
            horizon="24h",
            entry_price_usd=100.0,
            entry_ts=now,
            due_at=now,
            settled_at=now,
            exit_price_usd=100.0 * (1 + ret / 100.0),
            forward_return_pct=ret,
            gross_return_pct=ret,
            directional_return_pct=directional,
            estimated_fee_pct=0.1,
            estimated_spread_slippage_pct=0.1,
            estimated_funding_pct=0.0,
            net_return_pct=(directional - 0.2) if directional is not None else None,
            min_tradable_edge_pct=0.05,
            cost_model_version="v1",
            settlement_price_source="horizon_ohlcv",
            settlement_lag_sec=0.0,
            market_type="spot",
            status="settled",
        )

    repo.insert_forward_outcomes_batch([
        _outcome("AAA/USDT", None, 9.0),
        _outcome("BBB/USDT", None, -9.0),
        _outcome("CCC/USDT", "long", 3.0),
    ])

    from aletheia.agent_shell.trading_context import _get_data_health_summary
    summary = _get_data_health_summary()

    assert summary["signal_win_rate_24h"] == 100.0, (
        f"unknown-direction rows leaked into data-health win rate: {summary}"
    )
