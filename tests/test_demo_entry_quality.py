"""Exchange-demo entry quality: regime stand-down + family expectancy.

Boundary: new entries on bitget_demo / okx_demo / binance_testnet only.
paper_internal and live check #14 stay unchanged. Open positions are not closed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from aletheia.risk.demo_entry_quality import (
    FAMILY_NOT_DEMO_ELIGIBLE,
    REGIME_CONFIDENCE_STANDDOWN,
    REGIME_SIDEWAYS_STANDDOWN,
    REVERSE_AFTER_STOP,
    evaluate_demo_entry_quality,
    is_demo_entry_quality_skip,
    ranker_skip_reason,
)
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Opportunity, TradeProposal, UniverseForwardOutcome
from aletheia.storage.sqlite import reset_db


def _eligibility(**overrides):
    values = {
        "min_regime_confidence_to_enter": 0.60,
        "sideways_standdown": True,
        "family_min_samples": 20,
        "family_min_expectancy": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _tokens(violations):
    return [v.split(":", 1)[0] for v in violations]


def test_evaluate_ignores_paper_internal_and_live():
    eligibility = _eligibility()
    for mode in ("paper_internal", "bitget_live_limited", "okx_live_limited"):
        assert evaluate_demo_entry_quality(
            execution_mode=mode,
            eligibility=eligibility,
            scanner_family="short_bias",
            direction="short",
            regime_state="sideways",
            regime_confidence=0.50,
            family_edge={"n": 40, "avg_return": -1.2},
        ) == []


def test_evaluate_fail_open_when_regime_unpublished():
    assert evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        regime_state=None,
        regime_confidence=None,
        family_edge=None,
    ) == []


def test_evaluate_stands_down_published_sideways_and_low_confidence():
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        regime_state="sideways",
        regime_confidence=0.50,
        family_edge=None,
    )
    assert REGIME_CONFIDENCE_STANDDOWN in _tokens(violations)
    assert REGIME_SIDEWAYS_STANDDOWN in _tokens(violations)


def test_evaluate_mean_reversion_stands_down_published_sideways():
    """Sideways + mean_reversion was the loss cluster; MR is not exempt."""
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="mean_reversion",
        direction="long",
        regime_state="sideways",
        regime_confidence=0.80,
        family_edge=None,
    )
    assert REGIME_SIDEWAYS_STANDDOWN in _tokens(violations)
    assert REGIME_CONFIDENCE_STANDDOWN not in _tokens(violations)


def test_evaluate_mean_reversion_still_exempt_from_confidence_standdown():
    """Low-confidence trend is still fadeable; only sideways blocks MR."""
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="mean_reversion",
        direction="long",
        regime_state="bull",
        regime_confidence=0.50,
        family_edge=None,
    )
    assert violations == []


def test_evaluate_mean_reversion_still_blocked_by_negative_family_expectancy():
    """The family-expectancy gate still applies to mean_reversion — sideways
    standdown is separate; proven negative edge still blocks in a trend."""
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="mean_reversion",
        direction="long",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge={"n": 40, "avg_return": -1.5},
    )
    tokens = _tokens(violations)
    assert FAMILY_NOT_DEMO_ELIGIBLE in tokens
    assert all(not t.startswith(REGIME_CONFIDENCE_STANDDOWN) for t in tokens)
    assert all(not t.startswith(REGIME_SIDEWAYS_STANDDOWN) for t in tokens)


def test_evaluate_allows_published_bull_above_confidence_floor():
    violations = evaluate_demo_entry_quality(
        execution_mode="okx_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge=None,
    )
    assert violations == []


def test_evaluate_family_expectancy_fail_open_when_thin_or_missing():
    eligibility = _eligibility()
    assert evaluate_demo_entry_quality(
        execution_mode="binance_testnet",
        eligibility=eligibility,
        scanner_family="short_bias",
        direction="short",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge={"n": 19, "avg_return": -2.0},
    ) == []
    assert evaluate_demo_entry_quality(
        execution_mode="binance_testnet",
        eligibility=eligibility,
        scanner_family="short_bias",
        direction="short",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge=None,
    ) == []


def test_evaluate_rejects_negative_expectancy_with_enough_samples():
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="short_bias",
        direction="short",
        regime_state="bear",
        regime_confidence=0.80,
        family_edge={"n": 20, "avg_return": -0.4},
    )
    assert any(v.startswith(f"{FAMILY_NOT_DEMO_ELIGIBLE}:short_bias:short:") for v in violations)


def test_zero_or_none_thresholds_disable_matching_clause():
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(
            min_regime_confidence_to_enter=0.0,
            sideways_standdown=False,
            family_min_expectancy=None,
        ),
        scanner_family="short_bias",
        direction="short",
        regime_state="sideways",
        regime_confidence=0.10,
        family_edge={"n": 40, "avg_return": -3.0},
    )
    assert violations == []


def test_evaluate_rejects_same_symbol_reverse_after_stop():
    violations = evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="short_bias",
        direction="long",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge=None,
        recent_stop_directions={"short"},
    )
    assert any(v.startswith(f"{REVERSE_AFTER_STOP}:long:after_short_stop") for v in violations)


def test_evaluate_allows_same_direction_after_stop():
    assert evaluate_demo_entry_quality(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge=None,
        recent_stop_directions={"long"},
    ) == []


def test_evaluate_reverse_after_stop_ignores_live():
    assert evaluate_demo_entry_quality(
        execution_mode="bitget_live_limited",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        regime_state="bull",
        regime_confidence=0.80,
        family_edge=None,
        recent_stop_directions={"short"},
    ) == []


def test_ranker_skip_reason_uses_first_token():
    reason = ranker_skip_reason([
        f"{REGIME_CONFIDENCE_STANDDOWN}:conf=0.50<=0.60",
        f"{REGIME_SIDEWAYS_STANDDOWN}:state=sideways",
    ])
    assert reason.startswith(REGIME_CONFIDENCE_STANDDOWN)
    assert is_demo_entry_quality_skip(reason)
    assert is_demo_entry_quality_skip(f"{REVERSE_AFTER_STOP}:long:after_short_stop")
    assert not is_demo_entry_quality_skip("neutral_bias")


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.regime.detector import reset_state

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    reset_state()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _publish_regime(monkeypatch, *, state="sideways", confidence=0.50):
    from aletheia.regime.detector import MarketRegime
    import aletheia.regime.detector as det

    monkeypatch.setattr(
        det,
        "_published_regime",
        MarketRegime(state=state, confidence=confidence, signals={"source": "test"}),
    )


def _insert_family_outcomes(family, direction, *, n, net_return_pct, market_type="spot"):
    now = datetime.now(timezone.utc).isoformat()
    gross_return_pct = net_return_pct if direction == "long" else -net_return_pct
    for i in range(n):
        repo.insert_forward_outcome(UniverseForwardOutcome(
            member_id=f"m{i}",
            snapshot_id="snap1",
            symbol="BTC/USDT",
            venue="okx",
            scanner_family=family,
            direction=direction,
            horizon="24h",
            entry_price_usd=100.0,
            due_at=now,
            settled_at=now,
            exit_price_usd=100.0 + gross_return_pct,
            forward_return_pct=gross_return_pct,
            gross_return_pct=gross_return_pct,
            directional_return_pct=net_return_pct,
            estimated_fee_pct=0.0,
            estimated_spread_slippage_pct=0.0,
            estimated_funding_pct=0.0,
            net_return_pct=net_return_pct,
            min_tradable_edge_pct=0.05,
            settlement_price_source="horizon_ohlcv",
            settlement_lag_sec=0.0,
            cost_model_version="v1",
            market_type=market_type,
            status="settled",
        ))


def _proposal(execution_mode, *, family="momentum", direction="long"):
    opp = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias=direction,
        scanner_family=family,
    )
    repo.insert_opportunity(opp)
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        execution_mode=execution_mode,
        quantity=0.001,
        price=60000,
        opportunity_id=opp.id,
    )
    repo.insert_trade_proposal(proposal)
    return proposal


def test_risk_unpublished_regime_does_not_invent_standdown(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    result = evaluate_proposal(_proposal("bitget_demo"))
    assert not any(is_demo_entry_quality_skip(v) for v in result.violations), result.violations


def test_risk_bitget_demo_rejects_published_sideways(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    result = evaluate_proposal(_proposal("bitget_demo"))
    assert any(v.startswith(REGIME_SIDEWAYS_STANDDOWN) for v in result.violations)
    assert any(v.startswith(REGIME_CONFIDENCE_STANDDOWN) for v in result.violations)


def test_risk_paper_internal_ignores_published_sideways(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    result = evaluate_proposal(_proposal("paper_internal"))
    assert not any(is_demo_entry_quality_skip(v) for v in result.violations), result.violations


def test_risk_live_keeps_family_gate_not_demo_tokens(tmp_path, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "live"
    save_config(cfg)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    result = evaluate_proposal(_proposal("bitget_live_limited"))
    assert any("family_not_live_eligible" in v for v in result.violations)
    assert not any(is_demo_entry_quality_skip(v) for v in result.violations)


def test_risk_negative_family_expectancy_rejects_exchange_demo(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="bear", confidence=0.80)
    _insert_family_outcomes("short_bias", "short", n=20, net_return_pct=-1.5)
    result = evaluate_proposal(_proposal("okx_demo", family="short_bias", direction="short"))
    assert any(v.startswith(f"{FAMILY_NOT_DEMO_ELIGIBLE}:short_bias:short:") for v in result.violations)


def test_risk_thin_family_evidence_fail_open(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="bear", confidence=0.80)
    _insert_family_outcomes("short_bias", "short", n=19, net_return_pct=-1.5)
    result = evaluate_proposal(_proposal("okx_demo", family="short_bias", direction="short"))
    assert not any(v.startswith(FAMILY_NOT_DEMO_ELIGIBLE) for v in result.violations), result.violations


def test_risk_mean_reversion_stands_down_published_sideways(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.80)
    result = evaluate_proposal(_proposal("bitget_demo", family="mean_reversion", direction="long"))
    assert any(v.startswith(REGIME_SIDEWAYS_STANDDOWN) for v in result.violations)
    assert not any(v.startswith(REGIME_CONFIDENCE_STANDDOWN) for v in result.violations)


def test_risk_rejects_reverse_after_same_symbol_stop(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage.models import Position

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="bull", confidence=0.80)
    repo.insert_position(Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
        status="closed",
        realized_pnl=-5.0,
        close_reason="stop_loss",
        closed_at=datetime.now(timezone.utc).isoformat(),
    ))
    result = evaluate_proposal(_proposal("bitget_demo", family="momentum", direction="long"))
    assert any(v.startswith(f"{REVERSE_AFTER_STOP}:long:after_short_stop") for v in result.violations)


def test_null_demo_eligibility_migrates_to_shipped_defaults():
    from aletheia.config.schema import RiskConfig

    risk = RiskConfig.model_validate({
        "demo": {"eligibility": None},
        "live": {},
    })
    assert risk.demo.eligibility is not None
    assert risk.demo.eligibility.min_regime_confidence_to_enter == 0.60
    assert risk.demo.eligibility.sideways_standdown is True
    assert risk.demo.eligibility.family_min_samples == 20
    assert risk.demo.eligibility.family_min_expectancy == 0.0
    assert risk.live.eligibility is not None
    assert risk.live.eligibility.sideways_standdown is False
    assert risk.live.eligibility.family_min_expectancy is None
    assert risk.live.eligibility.family_min_win_rate_pct == 50.0


def test_flat_demo_aliases_write_nested_eligibility():
    from aletheia.config.schema import RiskConfig

    risk = RiskConfig.model_validate({})
    risk.demo_sideways_standdown = False
    risk.demo_min_regime_confidence_to_enter = 0.0
    risk.demo_family_min_samples = 0
    risk.demo_family_min_expectancy = None
    assert risk.demo.eligibility.sideways_standdown is False
    assert risk.demo.eligibility.min_regime_confidence_to_enter == 0.0
    assert risk.demo.eligibility.family_min_samples == 0
    assert risk.demo.eligibility.family_min_expectancy is None


def test_ranker_skips_bitget_demo_when_sideways_published(tmp_path, monkeypatch):
    from aletheia.agents.paper_trader_ranker import build_paper_candidates
    from aletheia.config.schema import AletheiaConfig

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = []
    cfg.trading.min_confidence_to_trade = "medium"
    opp = {
        "id": "opp-demo-1",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "status": "open",
        "direction_bias": "long",
        "confidence": "high",
        "confidence_score": 80,
        "execution_eligible": True,
        "scanner_family": "momentum",
        "source_agent": "divergence_scanner",
    }
    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_open_trade_opportunities", return_value=[opp]), \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "watch",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }), \
         patch(
             "aletheia.agents.paper_trader_ranker.verify_signal_emitter_admission",
             return_value=(True, "producer_eligibility_not_applicable"),
         ):
        candidates = build_paper_candidates(limit=10)
    assert len(candidates) == 1
    assert is_demo_entry_quality_skip(candidates[0]["skip_reason"])


def test_ranker_skips_mean_reversion_when_sideways_published(tmp_path, monkeypatch):
    """Mean-reversion in published sideways is now a demo standdown, not a target."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates
    from aletheia.config.schema import AletheiaConfig

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.80)
    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = []
    cfg.trading.min_confidence_to_trade = "medium"
    opp = {
        "id": "opp-mr-1",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "status": "open",
        "direction_bias": "long",
        "confidence": "high",
        "confidence_score": 80,
        "execution_eligible": True,
        "scanner_family": "mean_reversion",
        "source_agent": "divergence_scanner",
    }
    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_open_trade_opportunities", return_value=[opp]), \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "watch",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }), \
         patch(
             "aletheia.agents.paper_trader_ranker.verify_signal_emitter_admission",
             return_value=(True, "producer_eligibility_not_applicable"),
         ):
        candidates = build_paper_candidates(limit=10)
    assert len(candidates) == 1
    assert is_demo_entry_quality_skip(candidates[0]["skip_reason"])
    assert str(candidates[0]["skip_reason"]).startswith(REGIME_SIDEWAYS_STANDDOWN)


def test_ranker_paper_internal_does_not_skip_published_sideways(tmp_path, monkeypatch):
    from aletheia.agents.paper_trader_ranker import build_paper_candidates
    from aletheia.config.schema import AletheiaConfig

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = []
    cfg.trading.min_confidence_to_trade = "medium"
    opp = {
        "id": "opp-paper-1",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "status": "open",
        "direction_bias": "long",
        "confidence": "high",
        "confidence_score": 80,
        "execution_eligible": True,
        "scanner_family": "momentum",
        "source_agent": "divergence_scanner",
    }
    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_open_trade_opportunities", return_value=[opp]), \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "watch",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }), \
         patch(
             "aletheia.agents.paper_trader_ranker.verify_signal_emitter_admission",
             return_value=(True, "producer_eligibility_not_applicable"),
         ):
        candidates = build_paper_candidates(limit=10)
    assert len(candidates) == 1
    assert not is_demo_entry_quality_skip(candidates[0].get("skip_reason"))


def test_sideways_trend_tax_includes_short_bias():
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    sideways = MarketRegime(state="sideways", confidence=0.75)
    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=sideways):
        base = {"confidence_score": 50, "symbol": "ETH/USDT", "scanner_family": "short_bias"}
        score_short = _compute_trade_score(base, None, {"status": "watch"}, "medium", direction="short")
        score_rev = _compute_trade_score(
            dict(base, scanner_family="reversion"),
            None,
            {"status": "watch"},
            "medium",
            direction="short",
        )
    assert score_short < score_rev - 15


def test_paper_trader_treats_demo_quality_skips_as_permanent():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath(
        "aletheia", "agents", "paper_trader.py"
    ).read_text(encoding="utf-8")
    for token in (
        REGIME_CONFIDENCE_STANDDOWN,
        REGIME_SIDEWAYS_STANDDOWN,
        FAMILY_NOT_DEMO_ELIGIBLE,
        REVERSE_AFTER_STOP,
    ):
        assert token in src
