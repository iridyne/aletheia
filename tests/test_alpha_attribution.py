from __future__ import annotations

from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner


def _iso(days_ago: int = 0, hours_after: int = 0) -> str:
    anchor = datetime.fromisoformat("2026-07-12T07:10:01+00:00")
    return (anchor + timedelta(seconds=(30 - days_ago) * 10, hours=hours_after)).isoformat()


def _ms(iso_ts: str, hours_after: int = 0) -> int:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")) + timedelta(hours=hours_after)
    return int(dt.timestamp() * 1000)


def _insert_ohlcv(db, symbol: str, timeframe: str, entry_at: str, entry: float, exits: dict[int, float]) -> None:
    db.execute(
        "INSERT OR REPLACE INTO market_ohlcv "
        "(symbol, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'test')",
        (symbol, timeframe, _ms(entry_at), entry, entry, entry, entry),
    )
    for hours, close in exits.items():
        db.execute(
            "INSERT OR REPLACE INTO market_ohlcv "
            "(symbol, timeframe, ts, open, high, low, close, volume, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'test')",
            (symbol, timeframe, _ms(entry_at, hours), close, close, close, close),
        )


def _insert_shadow(
    db,
    batch: str,
    opp: str,
    rank: int,
    pnl: float,
    selected: int = 0,
    *,
    shadow_pnl: float | None = None,
    policy_epoch: str = "epoch-test",
    hours_after: int = 0,
) -> None:
    entry = _iso(days_ago=1, hours_after=hours_after)
    outcome_key = f"opportunity:{opp}"
    db.execute(
        "INSERT INTO shadow_trades "
        "(id, batch_id, opportunity_id, symbol, venue, market_type, direction, "
        "scanner_family, trade_score, rank, entry_price, outcome_candidate_key, "
        "was_selected, policy_epoch_id, created_at, pnl_pct, net_pnl_pct, "
        "cost_model_version, resolved_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'bitget', 'spot', 'long', 'momentum', ?, ?, "
        "100, ?, ?, ?, ?, ?, ?, 'candidate-net-v1', ?)",
        (
            f"sh-{batch}-{rank}-{opp}",
            batch,
            opp,
            100 - rank,
            rank,
            outcome_key,
            selected,
            policy_epoch,
            entry,
            pnl if shadow_pnl is None else shadow_pnl,
            pnl if shadow_pnl is None else shadow_pnl,
            _iso(),
        ),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '24h', 100, ?, ?, ?, ?, "
        "'candidate-net-v1', 'settled')",
        (
            f"fco-{batch}-{rank}-{opp}",
            outcome_key,
            entry,
            _iso(hours_after=24),
            _iso(),
            pnl,
        ),
    )


def _insert_operator_candidate(
    db,
    idx: int,
    decision: str,
    pnl: float,
    *,
    behavior: str = "behavior-test",
    hours_after: int = 0,
) -> None:
    decision_id = f"operator-{decision}-{idx}"
    key = f"pi_decision:{decision_id}"
    entry = _iso(days_ago=1, hours_after=hours_after)
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, created_at, symbol, direction, market_type, action, opportunity_id, "
        "signal_source, runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 'BTC/USDT', 'long', 'spot', ?, ?, 'market_observer', "
        "'session-test', 'commit-test', ?)",
        (
            decision_id,
            entry,
            "trade" if decision == "passed" else "skip",
            f"opp-operator-{decision}-{idx}",
            behavior,
        ),
    )
    db.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, venue, symbol, market_type, "
        "direction, filter_name, stage, sequence_no, decision, reason_code, "
        "input_snapshot, parameter_snapshot, filter_version, code_commit, "
        "outcome_candidate_key, evaluated_at) "
        "VALUES (?, ?, ?, 'pi_decision', 'bitget', 'BTC/USDT', 'spot', 'long', "
        "'operator_gate', 'system_operator', 1, ?, ?, '{}', '{}', 'operator-v1', "
        "'commit-test', ?, ?)",
        (f"fd-operator-{decision}-{idx}", f"dedup-operator-{decision}-{idx}", key, decision, decision, key, entry),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '24h', 100, ?, ?, ?, ?, "
        "'candidate-net-v1', 'settled')",
        (f"fco-operator-{decision}-{idx}", key, entry, _iso(hours_after=24), _iso(), pnl),
    )


def _insert_trade_chain(db, idx: int, pnl_pct: float, scanner: str = "momentum", closed_at: str | None = None) -> None:
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    opp_id = f"opp-{idx}"
    prop_id = f"prop-{idx}"
    pos_id = f"pos-{idx}"
    closed = closed_at or _iso(days_ago=1)
    opened = (datetime.fromisoformat(closed) - timedelta(hours=1)).isoformat()
    _insert_closed_position_via_producer(
        position_id=pos_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=pnl_pct,
        execution_mode="paper_internal",
        opened_at=opened,
        closed_at=closed,
        close_reason="signal_exit",
        proposal_id=prop_id,
        order_id=f"order-{idx}",
        opportunity_id=opp_id,
        scanner_family=scanner,
        confidence="high",
    )


def _insert_prospective_control_cohort(
    db,
    *,
    layer: str,
    prefix: str,
    selected_return: float,
    control_return: float,
) -> None:
    import json

    entry = _iso(days_ago=2)
    due_at = _iso(days_ago=2, hours_after=1)
    for i in range(5):
        for role, net_return in (
            ("selected", selected_return),
            ("control", control_return),
        ):
            symbol = f"{prefix}-{role}-{i}/USDT"
            outcome_key = f"{prefix}:{role}:{i}"
            db.execute(
                "INSERT INTO alpha_control_observations "
                "(id, layer, cohort_id, treatment_id, role, symbol, direction, horizon, "
                "entry_price, entry_ts, score, module_path, metadata, outcome_candidate_key) "
                "VALUES (?, ?, ?, ?, ?, ?, 'long', '1h', 100, ?, 50, ?, ?, ?)",
                (
                    f"aco-{prefix}-{role}-{i}",
                    layer,
                    f"cohort-{prefix}",
                    f"treatment-{prefix}",
                    role,
                    symbol,
                    entry,
                    f"aletheia/{layer}.py",
                    json.dumps({"due_at": due_at}),
                    outcome_key,
                ),
            )
            db.execute(
                "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
                "horizon, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, status) "
                "VALUES (?, ?, 'okx', ?, 'spot', 'long', '1h', 100, ?, ?, ?, ?, 'settled')",
                (
                    f"fco-{prefix}-{role}-{i}",
                    outcome_key,
                    symbol,
                    entry,
                    due_at,
                    due_at,
                    net_return,
                ),
            )


def test_empty_alpha_audit_returns_five_insufficient_verdicts():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit

    report = build_alpha_attribution_audit(days=30)

    assert set(report["verdicts"]) == {"universe", "ranker", "system_operator", "learning", "serenity"}
    assert all(v["verdict"] == "insufficient_data" for v in report["verdicts"].values())
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_ranker_alpha_detects_top1_underperforming_shadow_baseline():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"b{i}"
        _insert_shadow(db, batch, f"opp-r1-{i}", 1, -2.0, selected=1)
        _insert_shadow(db, batch, f"opp-r2-{i}", 2, 1.0)
        _insert_shadow(db, batch, f"opp-r3-{i}", 3, 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    ranker = report["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert ranker["sample_size"] == 5
    assert ranker["baseline"] > ranker["observed"]
    assert ranker["details"]["horizon"] == "24h"
    assert ranker["details"]["statistical_unit"] == "outcome_candidate_key"
    assert ranker["details"]["economic_unit"] == "net_return_pct"
    assert ranker["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_ranker_alpha_uses_canonical_fixed_horizon_outcome_not_shadow_lifecycle_pnl():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"fixed{i}"
        _insert_shadow(db, batch, f"fixed-r1-{i}", 1, -2.0, selected=1, shadow_pnl=50.0)
        _insert_shadow(db, batch, f"fixed-r2-{i}", 2, 2.0, shadow_pnl=-50.0)
    db.commit()

    ranker = build_alpha_attribution_audit(days=30)["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert ranker["observed"] == -2.0
    assert ranker["baseline"] == 2.0
    assert ranker["details"]["source_table"] == "filter_candidate_outcomes"


def test_ranker_alpha_does_not_mix_policy_epochs():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_shadow(db, f"old-{i}", f"old-r1-{i}", 1, -2.0, policy_epoch="epoch-old")
        _insert_shadow(db, f"old-{i}", f"old-r2-{i}", 2, 2.0, policy_epoch="epoch-old")
    _insert_shadow(db, "new-0", "new-r1-0", 1, 3.0, policy_epoch="epoch-new", hours_after=1)
    _insert_shadow(db, "new-0", "new-r2-0", 2, -3.0, policy_epoch="epoch-new", hours_after=1)
    db.commit()

    ranker = build_alpha_attribution_audit(days=30)["verdicts"]["ranker"]

    assert ranker["verdict"] == "insufficient_data"
    assert ranker["details"]["policy_epoch_id"] == "epoch-new"


def test_system_operator_alpha_compares_fixed_horizon_passed_and_rejected_candidates():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_operator_candidate(db, i, "passed", -3.0)
        _insert_operator_candidate(db, i, "rejected", 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    operator = report["verdicts"]["system_operator"]

    assert operator["verdict"] == "negative"
    assert operator["sample_size"] == 5
    assert operator["details"]["rejected_sample_size"] == 5
    assert operator["details"]["horizon"] == "24h"
    assert operator["details"]["statistical_unit"] == "outcome_candidate_key"
    assert operator["details"]["economic_unit"] == "net_return_pct"
    assert operator["baseline"] > operator["observed"]
    assert operator["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_system_operator_alpha_does_not_mix_behavior_fingerprints():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_operator_candidate(db, i, "passed", -3.0, behavior="behavior-old")
        _insert_operator_candidate(db, i, "rejected", 2.0, behavior="behavior-old")
    _insert_operator_candidate(db, 99, "passed", 3.0, behavior="behavior-new", hours_after=1)
    db.commit()

    operator = build_alpha_attribution_audit(days=30)["verdicts"]["system_operator"]

    assert operator["verdict"] == "insufficient_data"
    assert operator["details"]["behavior_fingerprint"] == "behavior-new"


def test_universe_alpha_uses_forward_outcomes_against_deterministic_baseline():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=2)
    for i in range(8):
        sym = f"BASE{i}/USDT"
        db.execute(
            "INSERT INTO universe_registry (id, venue, market_type, symbol, status, is_blacklisted) "
            "VALUES (?, 'bitget', 'spot', ?, 'active', 0)",
            (f"asset-base-{i}", sym),
        )
        _insert_ohlcv(db, sym, "1h", entry, 100.0, {1: 100.0})
    for i in range(5):
        db.execute(
            "INSERT INTO universe_forward_outcomes "
            "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
            "entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
            "VALUES (?, ?, 'snap', ?, 'momentum', 'long', '1h', 100, ?, ?, ?, 102, 2.0, 'settled')",
            (f"ufo-{i}", f"member-{i}", f"SEL{i}/USDT", entry, _iso(days_ago=2, hours_after=1), _iso(days_ago=1)),
        )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "positive"
    assert universe["sample_size"] == 5
    assert universe["baseline"] == 0.0
    assert universe["observed"] == 2.0


def test_universe_alpha_prefers_prospective_control_cohort():
    import json

    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=2)
    due_at = _iso(days_ago=2, hours_after=1)
    for i in range(5):
        sel = f"SELCAUSAL{i}/USDT"
        ctl = f"CTLCAUSAL{i}/USDT"
        _insert_ohlcv(db, sel, "1h", entry, 100.0, {1: 103.0})
        _insert_ohlcv(db, ctl, "1h", entry, 100.0, {1: 100.0})
        for role, sym in (("selected", sel), ("control", ctl)):
            outcome_key = f"control:{role}:{i}"
            db.execute(
                "INSERT INTO alpha_control_observations "
                "(id, layer, cohort_id, treatment_id, role, symbol, direction, horizon, "
                "entry_price, entry_ts, score, module_path, metadata, outcome_candidate_key) "
                "VALUES (?, 'universe', 'cohort-1', 'snap-1', ?, ?, 'long', '1h', 100, ?, 50, "
                "'aletheia/universe/allocator.py', ?, ?)",
                (f"aco-{role}-{i}", role, sym, entry, json.dumps({"due_at": due_at}), outcome_key),
            )
            db.execute(
                "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
                "horizon, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, status) "
                "VALUES (?, ?, 'okx', ?, 'spot', 'long', '1h', 100, ?, ?, ?, ?, 'settled')",
                (
                    f"fco-{role}-{i}", outcome_key, sym, entry, due_at, due_at,
                    3.0 if role == "selected" else 0.0,
                ),
            )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "positive"
    assert universe["causal_grade"] == "weak_observational"
    assert universe["evidence_type"] == "prospective_control_cohort"
    assert universe["baseline"] == 0.0
    assert universe["observed"] == 3.0
    assert universe["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_prospective_universe_negative_remains_directional_without_authority_contract():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_prospective_control_cohort(
        db,
        layer="universe",
        prefix="universe-negative",
        selected_return=-3.0,
        control_return=0.0,
    )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "negative"
    assert universe["contribution_verdict"] == "insufficient_data"
    assert universe["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_prospective_universe_and_ranker_negatives_remain_non_actionable():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_prospective_control_cohort(
        db,
        layer="universe",
        prefix="universe-authority",
        selected_return=-1.0,
        control_return=0.0,
    )
    for i in range(5):
        batch = f"weak-ranker-{i}"
        _insert_shadow(db, batch, f"weak-top1-{i}", 1, -10.0, selected=1)
        _insert_shadow(db, batch, f"weak-alt-{i}", 2, 1.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)

    assert report["verdicts"]["ranker"]["delta"] < report["verdicts"]["universe"]["delta"]
    assert report["verdicts"]["ranker"]["measurement_status"] == "directional_evidence_only"
    assert report["verdicts"]["universe"]["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_two_measured_negative_layers_are_not_ranked_by_raw_delta():
    from aletheia.analytics.alpha_attribution import _main_bottleneck

    bottleneck = _main_bottleneck({
        "universe": {
            "name": "Universe Alpha", "delta": -1.0,
            "contribution_verdict": "negative", "measurement_status": "measured_contribution",
        },
        "serenity": {
            "name": "Serenity Alpha", "delta": -5.0,
            "contribution_verdict": "negative", "measurement_status": "measured_contribution",
        },
    })

    assert bottleneck["layer"] == "not_comparable"
    assert set(bottleneck["candidate_layers"]) == {"Universe Alpha", "Serenity Alpha"}
    assert bottleneck["suspected_modules"] == []


def test_learning_alpha_detects_same_pattern_improvement_after_lesson():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    event_at = _iso(days_ago=10)
    for i in range(5):
        _insert_trade_chain(db, 100 + i, pnl_pct=-2.0, closed_at=_iso(days_ago=15))
    for i in range(5):
        _insert_trade_chain(db, 200 + i, pnl_pct=2.0, closed_at=_iso(days_ago=5))
    db.execute(
        "INSERT INTO ps_lessons "
        "(lesson_id, source_type, source_id, category, severity, summary, evidence_json, status, created_at, activated_at) "
        "VALUES ('lesson-alpha', 'trade_case_study', 'case-1', 'risk', 'warn', 'momentum improved', "
        "?, 'accepted', ?, ?)",
        ('{"scanner_family":"momentum","direction":"long","symbol":"BTC/USDT"}', event_at, event_at),
    )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    learning = report["verdicts"]["learning"]

    assert learning["verdict"] == "positive"
    assert learning["sample_size"] == 5
    assert learning["baseline"] == -2.0
    assert learning["observed"] == 2.0


def test_weak_negative_keeps_diagnostics_without_prescriptive_rendering():
    from aletheia.analytics.alpha_attribution import (
        build_alpha_attribution_audit,
        render_alpha_audit,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"module-b{i}"
        _insert_shadow(db, batch, f"opp-r1m-{i}", 1, -2.0, selected=1)
        _insert_shadow(db, batch, f"opp-r2m-{i}", 2, 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    ranker = report["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert "aletheia/agents/paper_trader_ranker.py" in ranker["suspected_modules"]
    assert report["main_bottleneck"]["layer"] == "insufficient_data"
    assert report["main_bottleneck"]["suspected_modules"] == []
    rendered = render_alpha_audit(report)
    assert "Suspect modules:" not in rendered
    assert "Inspect scoring weights" not in rendered
    assert "No actionable gradient is currently established" in rendered


def test_serenity_insert_records_prospective_alpha_controls():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=1)
    _insert_ohlcv(db, "AAPL", "1d", entry, 100.0, {24: 101.0})
    _insert_ohlcv(db, "MSFT", "1d", entry, 100.0, {24: 100.0})
    db.executemany(
        "INSERT INTO universe_registry "
        "(id, venue, market_type, symbol, status, is_blacklisted) "
        "VALUES (?, 'legacy_unknown', 'spot', ?, 'active', 0)",
        (("aapl-active", "AAPL"), ("msft-active", "MSFT")),
    )
    db.commit()

    opp = SerenityOpportunity(
        id="ser-alpha-1",
        theme_id="theme-alpha",
        symbol="AAPL",
        direction="long",
        asset_class="equity",
        created_at=entry,
    )
    repo.insert_serenity_opportunity(opp)

    rows = db.execute(
        "SELECT role, symbol, layer FROM alpha_control_observations "
        "WHERE layer = 'serenity' AND treatment_id = 'ser-alpha-1'"
    ).fetchall()
    assert any(r["role"] == "selected" and r["symbol"] == "AAPL" for r in rows)
    assert any(r["role"] == "control" and r["symbol"] == "MSFT" for r in rows)


def test_cli_and_api_surface_alpha_audit():
    from aletheia.main import app
    from aletheia.server.app import app as fastapi_app
    from aletheia.server.routes.analytics import get_alpha_attribution

    runner = CliRunner()
    result = runner.invoke(app, ["alpha", "audit", "--days", "7"])

    assert result.exit_code == 0
    assert "Alpha Attribution Audit" in result.output
    assert "System Operator:" in result.output

    assert any(route.path == "/api/analytics/alpha-attribution" for route in fastapi_app.routes)
    data = get_alpha_attribution(days=7)
    assert data["window_days"] == 7
    assert set(data["verdicts"]) == {"universe", "ranker", "system_operator", "learning", "serenity"}
