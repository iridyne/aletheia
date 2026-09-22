"""Exchange-demo entry quality: regime stand-down + family expectancy.

Boundary: new entries on bitget_demo / okx_demo / binance_testnet only.
The live family gate remains separate. Open positions are not closed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aletheia.risk.demo_entry_quality import (
    DEMO_ENTRY_QUALITY_UNAVAILABLE,
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


def test_evaluate_ignores_live():
    eligibility = _eligibility()
    for mode in ("bitget_live_limited", "okx_live_limited"):
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


def test_collect_keeps_honest_empty_evidence_fail_open(monkeypatch):
    import extensions.learning.demo_entry_quality as policy
    import aletheia.risk.demo_entry_quality as quality
    import aletheia.risk.engine as engine

    monkeypatch.setattr(policy, "ensure_demo_entry_quality_policy_epoch", lambda: "epoch")
    monkeypatch.setattr(quality, "load_published_regime", lambda **_: (None, None))
    monkeypatch.setattr(engine, "_family_live_edge", lambda *args, **kwargs: None)
    monkeypatch.setattr(quality, "load_recent_stop_directions", lambda *args, **kwargs: set())

    assert quality.collect_demo_entry_quality_violations(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        symbol="BTC/USDT",
    ) == []


def test_collect_marks_each_unreadable_authority_source(monkeypatch):
    import extensions.learning.demo_entry_quality as policy
    import aletheia.risk.demo_entry_quality as quality
    import aletheia.risk.engine as engine

    def explode(*args, **kwargs):
        raise RuntimeError("read failed")

    monkeypatch.setattr(policy, "ensure_demo_entry_quality_policy_epoch", explode)
    monkeypatch.setattr(quality, "load_published_regime", explode)
    monkeypatch.setattr(engine, "_family_live_edge", explode)
    monkeypatch.setattr(quality, "load_recent_stop_directions", explode)

    violations = quality.collect_demo_entry_quality_violations(
        execution_mode="bitget_demo",
        eligibility=_eligibility(),
        scanner_family="momentum",
        direction="long",
        symbol="BTC/USDT",
    )

    assert set(violations) == {
        f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:policy_epoch",
        f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:regime",
        f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:family_edge",
        f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:recent_stops",
    }


def test_strict_regime_read_uses_the_authoritative_row_once(monkeypatch):
    import extensions.regime.detector as detector
    import aletheia.storage.repositories as repositories
    import aletheia.risk.demo_entry_quality as quality

    detector.reset_state()
    monkeypatch.setattr(
        repositories,
        "get_latest_market_regime",
        lambda: {"state": "bull", "confidence": 0.80},
    )

    def unexpected_second_read():
        raise AssertionError("strict regime recovery must not read the repository twice")

    monkeypatch.setattr(detector, "get_current_regime", unexpected_second_read)

    assert quality.load_published_regime(fail_open=False) == ("bull", 0.80)


def test_strict_regime_read_rejects_non_finite_confidence(monkeypatch):
    import extensions.regime.detector as detector
    import aletheia.storage.repositories as repositories
    import aletheia.risk.demo_entry_quality as quality

    detector.reset_state()
    monkeypatch.setattr(
        repositories,
        "get_latest_market_regime",
        lambda: {"state": "bull", "confidence": "nan"},
    )

    with pytest.raises(ValueError, match="finite"):
        quality.load_published_regime(fail_open=False)


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
    from extensions.regime.detector import reset_state

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
    from extensions.regime.detector import MarketRegime
    import extensions.regime.detector as det

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


def test_risk_read_failure_blocks_new_demo_entry(tmp_path, monkeypatch):
    import extensions.learning.demo_entry_quality as policy
    import aletheia.risk.demo_entry_quality as quality
    import aletheia.risk.engine as engine
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)

    def explode(*args, **kwargs):
        raise RuntimeError("read failed")

    monkeypatch.setattr(policy, "ensure_demo_entry_quality_policy_epoch", lambda: "epoch")
    monkeypatch.setattr(quality, "load_published_regime", lambda **_: ("bull", 0.80))
    monkeypatch.setattr(engine, "_family_live_edge", lambda *args, **kwargs: None)
    monkeypatch.setattr(quality, "load_recent_stop_directions", lambda *args, **kwargs: set())

    for source, target, name in (
        ("policy_epoch", policy, "ensure_demo_entry_quality_policy_epoch"),
        ("regime", quality, "load_published_regime"),
        ("family_edge", engine, "_family_live_edge"),
        ("recent_stops", quality, "load_recent_stop_directions"),
    ):
        with monkeypatch.context() as isolated:
            isolated.setattr(target, name, explode)
            result = evaluate_proposal(_proposal("bitget_demo"))
        assert not result.approved
        assert f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:{source}" in result.violations


def test_risk_bitget_demo_rejects_published_sideways(tmp_path, monkeypatch):
    from aletheia.risk.engine import evaluate_proposal

    setup_env(tmp_path, monkeypatch)
    _publish_regime(monkeypatch, state="sideways", confidence=0.50)
    result = evaluate_proposal(_proposal("bitget_demo"))
    assert any(v.startswith(REGIME_SIDEWAYS_STANDDOWN) for v in result.violations)
    assert any(v.startswith(REGIME_CONFIDENCE_STANDDOWN) for v in result.violations)


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


def test_retired_scope_rotates_epoch_without_rewriting_history():
    import json
    from extensions.learning import demo_entry_quality as policy
    from extensions.learning.policy_epochs import build_policy_epoch, persist_policy_epoch
    from aletheia.storage.sqlite import get_db

    # The previous persisted policy included a promise about the local simulator.
    legacy_values = {
        **policy.demo_entry_quality_policy_context(),
        "version": "v3", "paper_internal_unchanged": True,
    }
    legacy = build_policy_epoch(
        "demo_entry_quality", legacy_values,
        affected_path_ids=policy._AFFECTED_PATHS, safety_required=True,
    )
    legacy_id = persist_policy_epoch(legacy)
    current_id = policy.ensure_demo_entry_quality_policy_epoch()
    assert current_id != legacy_id
    assert policy.ensure_demo_entry_quality_policy_epoch() == current_id
    rows = {row["epoch_id"]: dict(row) for row in get_db().execute(
        "SELECT * FROM learning_policy_epochs WHERE epoch_kind='demo_entry_quality'"
    )}
    assert len(rows) == 2
    assert rows[legacy_id]["effective_values_hash"] == legacy.epoch_hash
    assert json.loads(rows[legacy_id]["effective_values_json"]) == legacy_values
    assert rows[legacy_id]["ended_at"] is not None
    assert rows[current_id]["ended_at"] is None
    current_values = json.loads(rows[current_id]["effective_values_json"])
    assert current_values["version"] == "v4"
    assert "paper_internal_unchanged" not in current_values
