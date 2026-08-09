"""Unit tests for aletheia.agents.paper_trader_ranker."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def _make_opp(
    *,
    oid="opp-1",
    symbol="BTC/USDT",
    asset_class="crypto",
    status="open",
    direction_bias="long",
    confidence="high",
    confidence_score=80,
    execution_eligible=True,
    linked_strategy_id=None,
    universe_member_id=None,
    scanner_family="momentum",
    source_agent="market_observer",
    valid_until=None,
):
    return {
        "id": oid,
        "symbol": symbol,
        "asset_class": asset_class,
        "status": status,
        "direction_bias": direction_bias,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "execution_eligible": execution_eligible,
        "linked_strategy_id": linked_strategy_id,
        "universe_member_id": universe_member_id,
        "scanner_family": scanner_family,
        "source_agent": source_agent,
        "valid_until": valid_until,
    }


def _mock_config():
    cfg = MagicMock()
    cfg.risk.symbol_filter_mode = "whitelist"
    # The ranker's symbol filter must mirror the risk engine's check #1, which
    # reads cfg.risk.symbol_list (NOT the exchange-level list).
    cfg.risk.symbol_list = ["BTC/USDT", "ETH/USDT"]
    cfg.risk.demo.positions.max_open_positions = 15
    cfg.risk.demo.capital.max_per_symbol_notional_usdt = 400.0
    cfg.risk.demo.capital.max_position_notional_usdt = 50.0
    cfg.trading.min_confidence_to_trade = "medium"
    return cfg


def _patch_deps(opps, strategies=None, universe_member=None, factor_status="watch",
                open_positions=None):
    """Return a context manager that patches all ranker dependencies."""
    patches = {}
    # Position C (2026-07-02): the ranker no longer resolves StrategyRecords, so
    # list_strategies / get_strategy are no longer module attributes to patch.
    patches["aletheia.agents.paper_trader_ranker.load_config"] = MagicMock(return_value=_mock_config())
    patches["aletheia.agents.paper_trader_ranker.list_open_trade_opportunities"] = MagicMock(return_value=opps)
    patches["aletheia.agents.paper_trader_ranker.get_universe_member"] = MagicMock(return_value=universe_member)
    # Portfolio pre-filter context: an empty local book by default, overridable
    # via open_positions for the capacity / same-symbol / notional tests.
    patches["aletheia.agents.paper_trader_ranker.list_all_open_positions"] = MagicMock(
        return_value=open_positions or []
    )
    patches["aletheia.agents.paper_trader_ranker.lookup_factor_signal"] = MagicMock(return_value={
        "factor_key": "test", "status": factor_status,
        "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
    })

    class MultiPatch:
        def __enter__(self):
            self._patchers = [patch(k, v) for k, v in patches.items()]
            for p in self._patchers:
                p.start()
            return self

        def __exit__(self, *args):
            for p in self._patchers:
                p.stop()

    return MultiPatch()


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

def test_equity_shadow_tracked():
    """Equity opportunities are scored but not executed (equity_shadow_track skip_reason)."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [_make_opp(asset_class="equity", direction_bias="long", confidence="high")]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "equity_shadow_track"
    assert select_best_candidate(candidates) is None


def test_neutral_bias_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(direction_bias="neutral")]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "neutral_bias"


def test_low_confidence_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(confidence="speculative", confidence_score=20)]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert "confidence_below" in candidates[0]["skip_reason"]


def test_expired_universe_member_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(universe_member_id="um-1")]
    expired_member = {
        "id": "um-1", "execution_eligible": True,
        "expires_at": "2020-01-01T00:00:00+00:00",  # long expired
        "rank": 1, "total_score": 80.0, "tradability_score": 70.0,
        "late_entry_penalty": 0, "crowding_risk": 0, "risk_flags": "[]",
    }
    with _patch_deps(opps, universe_member=expired_member):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "ttl_expired"


def test_expired_opportunity_valid_until_skipped_without_member():
    """Opportunity.valid_until remains authoritative if its member row is unavailable."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(valid_until="2020-01-01T00:00:00+00:00")]
    with _patch_deps(opps, universe_member=None):
        candidates = build_paper_candidates(limit=50)

    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "ttl_expired"
    assert candidates[0]["filter_trace"][-1]["reason_code"] == "ttl_expired"
    assert candidates[0]["filter_trace"][-1]["parameters"]["source"] == "opportunity.valid_until"


def test_malformed_opportunity_ttl_fails_closed_with_truthful_reason():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(valid_until="not-an-iso-timestamp")]
    with _patch_deps(opps, universe_member=None):
        candidate = build_paper_candidates(limit=50)[0]

    assert candidate["skip_reason"] == "ttl_invalid"
    ttl_step = next(
        step for step in candidate["filter_trace"]
        if step["filter_name"] == "family_ttl"
    )
    assert ttl_step["decision"] == "error"
    assert ttl_step["reason_code"] == "ttl_timestamp_invalid"


def test_blocked_factor_diagnostic_is_neutral_without_authority():
    """Retrospective blocked_candidate does not change score without prospective authority."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp()]

    # A diagnostic status without authority must retain the static baseline.
    with _patch_deps(opps, factor_status="blocked_candidate"):
        blocked_candidates = build_paper_candidates(limit=50)
    assert len(blocked_candidates) == 1
    assert blocked_candidates[0]["skip_reason"] is None  # no hard-skip

    # Compare with "active" status: neither un-authorized label changes score.
    with _patch_deps(opps, factor_status="active"):
        active_candidates = build_paper_candidates(limit=50)
    assert len(active_candidates) == 1
    assert blocked_candidates[0]["trade_score"] == active_candidates[0]["trade_score"]


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

def test_highest_trade_score_selected():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [
        _make_opp(oid="opp-1", confidence_score=60),
        _make_opp(oid="opp-2", confidence_score=90),
        _make_opp(oid="opp-3", confidence_score=40),
    ]
    with _patch_deps(opps, factor_status="active"):
        candidates = build_paper_candidates(limit=50)
        best = select_best_candidate(candidates)

    assert best is not None
    assert best["opportunity_id"] == "opp-2"
    assert best["trade_score"] > candidates[-1]["trade_score"]


def test_missing_factor_history_does_not_block():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [_make_opp()]
    with _patch_deps(opps, factor_status="watch"):
        candidates = build_paper_candidates(limit=50)
        best = select_best_candidate(candidates)

    assert best is not None
    assert best["skip_reason"] is None


@pytest.mark.parametrize(
    ("applicability", "evaluation", "expected_decision", "expected_reason"),
    [
        (
            ("not_applicable", "learning_influence_disabled"),
            None,
            "not_applicable",
            "learning_influence_disabled",
        ),
        (
            ("enabled", "guard_evaluation_enabled"),
            None,
            "error",
            "guard_evaluation_failed",
        ),
    ],
)
def test_guard_trace_never_calls_disabled_or_failed_evaluation_passed(
    applicability,
    evaluation,
    expected_decision,
    expected_reason,
):
    """No evaluation is not evidence that the candidate passed the Guard."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    with _patch_deps([_make_opp()]), patch(
        "aletheia.agents.paper_trader_ranker._guard_evaluation_applicability",
        return_value=applicability,
    ), patch(
        "aletheia.agents.paper_trader_ranker._evaluate_guards_safe",
        return_value=evaluation,
    ):
        candidate = build_paper_candidates(limit=1)[0]

    guard_step = next(
        step
        for step in candidate["filter_trace"]
        if step["filter_name"] == "guard_candidate_block"
    )
    assert guard_step["decision"] == expected_decision
    assert guard_step["reason_code"] == expected_reason


def test_select_best_candidate_none_when_all_skipped():
    from aletheia.agents.paper_trader_ranker import select_best_candidate

    candidates = [
        {"skip_reason": "neutral_bias", "trade_score": -999},
        {"skip_reason": "blocked_factor:test", "trade_score": -999},
    ]
    assert select_best_candidate(candidates) is None


def test_execution_not_eligible_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(execution_eligible=False)]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "execution_eligible=False"


@pytest.mark.parametrize(
    ("filter_mode", "configured_symbols"),
    [
        pytest.param("whitelist", ["BTC/USDT"], id="whitelist-miss"),
        pytest.param("blacklist", ["SOL/USDT"], id="blacklist-hit"),
    ],
)
def test_symbol_policy_rejection_is_durable_and_terminal(
    tmp_path,
    monkeypatch,
    filter_mode,
    configured_symbols,
):
    """A policy-rejected stored Opportunity must not disappear between ranker and evidence."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = configured_symbols
    cfg.risk.symbol_filter_mode = filter_mode
    save_config(cfg)

    opportunity = Opportunity(
        id=f"opp-symbol-{filter_mode}",
        symbol="SOL/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=88,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)

    asyncio.run(
        run_paper_trader(
            permission_mode="Paper",
            run_id=f"symbol-policy-{filter_mode}",
        )
    )

    status = get_opportunity(opportunity.id)["status"]
    rows = [
        dict(row)
        for row in get_db().execute(
            "SELECT filter_name, decision, reason_code FROM filter_decisions "
            "WHERE opportunity_id=? ORDER BY sequence_no",
            (opportunity.id,),
        ).fetchall()
    ]
    symbol_rejections = [
        row
        for row in rows
        if row["decision"] == "rejected"
        and "symbol" in f"{row['filter_name']} {row['reason_code']}"
        and row["reason_code"]
    ]
    failures = []
    if not symbol_rejections:
        failures.append(f"no durable symbol rejection was written (rows={rows!r})")
    if status == "open":
        failures.append("Opportunity silently remained open")
    assert not failures, f"{filter_mode}: " + "; ".join(failures)


def test_symbol_policy_evidence_failure_leaves_opportunity_retryable(
    tmp_path, monkeypatch
):
    """A permanent state must not commit when its mandatory reason did not."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="opp-symbol-evidence-failure",
        symbol="SOL/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=88,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)

    def fail_evidence(*_args, **_kwargs):
        raise RuntimeError("forced evidence write failure")

    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.record_candidate_filter_trace",
        fail_evidence,
    )

    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="symbol-evidence-failure")
    )

    assert get_opportunity(opportunity.id)["status"] == "open"
    count = get_db().execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    assert count == 0
    assert any(
        error.startswith("permanent_skip_evidence_failed:")
        for error in result["errors"]
    )


def test_permanent_rejection_sweep_covers_complete_open_workset(
    tmp_path, monkeypatch
):
    """The execution shortlist must not leave a silent permanent-reject tail."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    def insert_rejected(prefix: str, count: int) -> list[str]:
        ids = []
        for index in range(count):
            opportunity = Opportunity(
                id=f"{prefix}-{index:03d}",
                symbol=f"REJECT{index}/USDT",
                market="spot",
                direction_bias="long",
                confidence="high",
                confidence_score=88,
                source_agent="market_observer",
                asset_class="crypto",
                execution_eligible=True,
                status="open",
            )
            insert_opportunity(opportunity)
            ids.append(opportunity.id)
        return ids

    original_ids = insert_rejected("old-symbol-reject", 251)
    asyncio.run(run_paper_trader(permission_mode="Paper", run_id="terminal-sweep-1"))

    placeholders = ",".join("?" for _ in original_ids)
    still_open_after_first = [
        row[0]
        for row in get_db().execute(
            f"SELECT id FROM opportunities WHERE status='open' AND id IN ({placeholders})",
            original_ids,
        ).fetchall()
    ]
    assert still_open_after_first == ["old-symbol-reject-250"]

    asyncio.run(run_paper_trader(permission_mode="Paper", run_id="terminal-sweep-2"))
    still_open = [
        row[0]
        for row in get_db().execute(
            f"SELECT id FROM opportunities WHERE status='open' AND id IN ({placeholders})",
            original_ids,
        ).fetchall()
    ]
    evidence = get_db().execute(
        f"SELECT COUNT(DISTINCT opportunity_id) FROM filter_decisions "
        f"WHERE opportunity_id IN ({placeholders}) "
        "AND filter_name='symbol_filter' AND decision='rejected' "
        "AND reason_code='symbol_not_allowed'",
        original_ids,
    ).fetchone()[0]
    assert still_open == []
    assert evidence == len(original_ids)


def test_prediction_observations_do_not_starve_trade_fifo_or_change_lifecycle(
    tmp_path, monkeypatch
):
    """Observation-only prediction rows must stay outside the paper trade workset."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    oldest = datetime.now(timezone.utc) - timedelta(hours=1)
    for index in range(250):
        insert_opportunity(
            Opportunity(
                id=f"prediction-observation-{index:03d}",
                symbol="PREDICTION",
                market="prediction",
                direction_bias=None,
                confidence="medium",
                confidence_score=60,
                source_agent="prediction_observer",
                asset_class="prediction",
                execution_eligible=False,
                status="open",
                created_at=(oldest + timedelta(seconds=index)).isoformat(),
            )
        )

    tradeable = Opportunity(
        id="crypto-behind-prediction-backlog",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=92,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    insert_opportunity(tradeable)

    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="prediction-fifo-boundary")
    )

    db = get_db()
    queued = db.execute(
        "SELECT opportunity_id, market_type FROM pi_decisions "
        "WHERE action='signal_pending' AND opportunity_id=?",
        (tradeable.id,),
    ).fetchall()
    prediction_states = db.execute(
        "SELECT status, COUNT(*) AS n FROM opportunities "
        "WHERE asset_class='prediction' GROUP BY status"
    ).fetchall()
    prediction_evidence = db.execute(
        "SELECT COUNT(*) FROM filter_decisions fd JOIN opportunities o "
        "ON o.id=fd.opportunity_id WHERE o.asset_class='prediction'"
    ).fetchone()[0]

    assert [dict(row) for row in queued] == [
        {"opportunity_id": tradeable.id, "market_type": "spot"}
    ], result
    assert [dict(row) for row in prediction_states] == [{"status": "open", "n": 250}]
    assert prediction_evidence == 0


def test_other_signal_source_with_same_bare_id_does_not_consume_market_opportunity(
    tmp_path, monkeypatch
):
    """A Serenity final cannot terminalize a market-observer Opportunity with the same id."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="shared-cross-source-id",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="serenity-final-with-colliding-id",
        action="skip",
        opportunity_id=opportunity.id,
        signal_source="serenity",
        symbol="ETH/USDT",
        direction="long",
        market_type="spot",
        conviction=40,
        reasoning="unrelated Serenity decision",
        proposal_status="not_applicable",
    ))

    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="cross-source-id")
    )

    pending = get_db().execute(
        "SELECT opportunity_id, signal_source FROM pi_decisions "
        "WHERE action='signal_pending' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    assert get_opportunity(opportunity.id)["status"] == "queued", result
    assert [dict(row) for row in pending] == [{
        "opportunity_id": opportunity.id,
        "signal_source": "market_observer",
    }]


def test_retryable_prefix_rotates_in_bounded_batches_until_tail_is_reached(
    tmp_path, monkeypatch
):
    """Each run is bounded, while repeated runs eventually reach a retryable tail."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity, Position
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_position,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["HELD/USDT", "BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    insert_position(Position(
        id="held-position",
        symbol="HELD/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    ))
    oldest = datetime.now(timezone.utc) - timedelta(hours=1)
    for index in range(501):
        insert_opportunity(Opportunity(
            id=f"retryable-held-{index:03d}",
            symbol="HELD/USDT",
            market="spot",
            direction_bias="long",
            confidence="high",
            confidence_score=90,
            source_agent="market_observer",
            asset_class="crypto",
            execution_eligible=True,
            status="open",
            created_at=(oldest + timedelta(seconds=index)).isoformat(),
        ))
    target = Opportunity(
        id="eligible-behind-retryable-front-page",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=89,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    insert_opportunity(target)

    first = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="retryable-rotation-1")
    )
    assert get_opportunity(target.id)["status"] == "open", first
    first_ranked = get_db().execute(
        "SELECT COUNT(*) FROM opportunities WHERE last_ranked_at IS NOT NULL"
    ).fetchone()[0]
    assert first_ranked == 250

    second = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="retryable-rotation-2")
    )
    assert get_opportunity(target.id)["status"] == "open", second
    second_ranked = get_db().execute(
        "SELECT COUNT(*) FROM opportunities WHERE last_ranked_at IS NOT NULL"
    ).fetchone()[0]
    assert second_ranked == 375

    third = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="retryable-rotation-3")
    )
    assert get_opportunity(target.id)["status"] == "open", third
    third_ranked = get_db().execute(
        "SELECT COUNT(*) FROM opportunities WHERE last_ranked_at IS NOT NULL"
    ).fetchone()[0]
    assert third_ranked == 500

    fourth = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="retryable-rotation-4")
    )

    pending = get_db().execute(
        "SELECT opportunity_id FROM pi_decisions "
        "WHERE action='signal_pending' AND opportunity_id=?",
        (target.id,),
    ).fetchall()
    assert get_opportunity(target.id)["status"] == "queued", fourth
    assert [row[0] for row in pending] == [target.id]
    batches = get_db().execute(
        "SELECT run_id, candidate_count FROM filter_decision_batches "
        "WHERE filter_name='paper_trader_workset' ORDER BY evaluated_at, rowid"
    ).fetchall()
    assert [tuple(row) for row in batches] == [
        ("retryable-rotation-1", 250),
        ("retryable-rotation-2", 250),
        ("retryable-rotation-3", 250),
        # The last two never-seen rows lead the fourth run; the remaining bounded
        # capacity then revisits the least-recently ranked retryable rows.
        ("retryable-rotation-4", 250),
    ]
    retryable_states = get_db().execute(
        "SELECT status, COUNT(*) FROM opportunities "
        "WHERE id LIKE 'retryable-held-%' GROUP BY status"
    ).fetchall()
    assert [tuple(row) for row in retryable_states] == [("open", 501)]


def test_sustained_new_arrivals_cannot_starve_old_retryable_opportunity(
    tmp_path, monkeypatch
):
    """A fixed retry quota must advance old open work even when 250 new rows arrive."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity, Position
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_position,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["HELD/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    insert_position(Position(
        id="sustained-arrival-held-position",
        symbol="HELD/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    ))
    initial_cursor = "2020-01-01T00:00:00+00:00"
    old_retry = Opportunity(
        id="old-retry-under-sustained-arrivals",
        symbol="HELD/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
        created_at="2019-12-31T00:00:00+00:00",
        last_ranked_at=initial_cursor,
    )
    insert_opportunity(old_retry)

    arrival_base = datetime.now(timezone.utc) - timedelta(minutes=10)
    for run_index in range(3):
        for row_index in range(250):
            serial = run_index * 250 + row_index
            insert_opportunity(Opportunity(
                id=f"sustained-new-{serial:04d}",
                symbol="HELD/USDT",
                market="spot",
                direction_bias="long",
                confidence="high",
                confidence_score=80,
                source_agent="market_observer",
                asset_class="crypto",
                execution_eligible=True,
                status="open",
                created_at=(arrival_base + timedelta(seconds=serial)).isoformat(),
            ))
        result = asyncio.run(run_paper_trader(
            permission_mode="Paper",
            run_id=f"sustained-arrivals-{run_index + 1}",
        ))
        assert result["errors"] == []

    stored = get_opportunity(old_retry.id)
    batches = get_db().execute(
        "SELECT candidate_count FROM filter_decision_batches "
        "WHERE run_id LIKE 'sustained-arrivals-%' ORDER BY run_id"
    ).fetchall()
    assert stored["status"] == "open"
    assert stored["last_ranked_at"] != initial_cursor, (
        "old retryable work never received a slot while each run added 250 NULL cursors"
    )
    assert [row[0] for row in batches] == [250, 250, 250]


def test_open_trade_rotation_queries_use_matching_indexes_at_scale(
    tmp_path, monkeypatch
):
    """Both workset halves must avoid a table scan and temporary ORDER BY tree."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    db = get_db()
    with repo.transaction():
        db.executemany(
            "INSERT INTO opportunities "
            "(id,symbol,market,direction_bias,confidence,status,asset_class,"
            "created_at,last_ranked_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"plan-unranked-{index:04d}", "BTC/USDT", "spot", "long",
                    "high", "open", "crypto",
                    f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
                    None,
                )
                for index in range(1000)
            ] + [
                (
                    f"plan-retry-{index:04d}", "ETH/USDT", "spot", "long",
                    "high", "open", "crypto",
                    f"2026-01-02T00:{index // 60:02d}:{index % 60:02d}+00:00",
                    f"2026-01-03T00:{index // 60:02d}:{index % 60:02d}+00:00",
                )
                for index in range(1000)
            ],
        )

    expected_indexes = {
        "idx_opportunities_trade_unranked_rotation",
        "idx_opportunities_trade_retry_rotation",
    }
    for index_name in expected_indexes:
        db.execute(f"DROP INDEX IF EXISTS {index_name}")
    # This is the existing-database path: the Opportunity table/column already
    # exist, and rerunning migrations must restore both optimized indexes.
    run_migrations()

    traced: list[str] = []
    db.set_trace_callback(traced.append)
    try:
        rows = repo.list_open_trade_opportunities(limit=250)
    finally:
        db.set_trace_callback(None)
    workset_queries = [
        sql for sql in traced
        if sql.lstrip().upper().startswith("SELECT * FROM OPPORTUNITIES")
    ]
    plan_details = []
    for sql in workset_queries:
        plan_details.extend(
            row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
        )
    index_names = {
        row[1] for row in db.execute("PRAGMA index_list(opportunities)").fetchall()
    }
    failures = []
    if len(rows) != 250:
        failures.append(f"bounded workset returned {len(rows)} rows")
    if len(workset_queries) < 2:
        failures.append(
            f"expected separate never/retry queries, captured {len(workset_queries)}"
        )
    if expected_indexes - index_names:
        failures.append(f"missing indexes {sorted(expected_indexes - index_names)!r}")
    if any("SCAN opportunities" in detail for detail in plan_details):
        failures.append("query plan still scans opportunities")
    if any("USE TEMP B-TREE" in detail for detail in plan_details):
        failures.append("query plan still builds a temporary ORDER BY tree")
    assert not failures, f"{'; '.join(failures)}; plans={plan_details!r}"


def test_accepted_candidate_evidence_failure_prevents_queue_transition(
    tmp_path, monkeypatch
):
    """A candidate cannot become executable when its filter/selection facts are absent."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="accepted-evidence-outage",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)

    def fail_evidence(*_args, **_kwargs):
        raise RuntimeError("forced accepted evidence outage")

    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.record_candidate_filter_trace",
        fail_evidence,
    )

    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="accepted-evidence-outage")
    )

    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions "
        "WHERE action='signal_pending' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    evidence_count = get_db().execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    assert get_opportunity(opportunity.id)["status"] == "open", result
    assert pending_count == 0
    assert evidence_count == 0
    assert any(
        error.startswith("accepted_filter_evidence_failed:")
        for error in result["errors"]
    )


def test_low_level_filter_evidence_failure_cannot_publish_pending_signal(
    tmp_path, monkeypatch
):
    """A swallowed repository error must not make the current run look durable."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="low-level-evidence-write-failure",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)

    from aletheia.analytics import filter_evidence

    original_insert = filter_evidence.repo.insert_filter_decision
    insert_calls = 0

    def fail_one_repository_insert(*args, **kwargs):
        nonlocal insert_calls
        insert_calls += 1
        if insert_calls == 2:
            raise RuntimeError("forced filter_decisions repository failure")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.repo.insert_filter_decision",
        fail_one_repository_insert,
    )

    run_id = "low-level-evidence-write-failure"
    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id=run_id)
    )

    db = get_db()
    pending_count = db.execute(
        "SELECT COUNT(*) FROM pi_decisions "
        "WHERE action='signal_pending' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    individual_count = db.execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    batch_count = db.execute(
        "SELECT COUNT(*) FROM filter_decision_batches WHERE run_id=?",
        (run_id,),
    ).fetchone()[0]
    stored = get_opportunity(opportunity.id)

    assert stored["status"] == "open", result
    assert stored["last_ranked_at"] is None
    assert pending_count == 0
    assert individual_count == 0
    assert batch_count == 0
    assert insert_calls == 2
    assert any(
        error.startswith("accepted_filter_evidence_failed:")
        for error in result["errors"]
    )


def test_historic_individual_evidence_cannot_replace_current_run_batch(
    tmp_path, monkeypatch
):
    """A failed current-run batch rolls back rotation and writes no queue carrier."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.agents.paper_trader_ranker import build_paper_candidate_workset
    from aletheia.analytics import filter_evidence
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="historic-evidence-must-not-authorize-current-run",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
    )
    insert_opportunity(opportunity)
    candidate = build_paper_candidate_workset(limit=50).scanned_candidates[0]
    filter_evidence.record_candidate_filter_trace(candidate, run_id="historic-run")
    filter_evidence.record_candidate_filter_trace(
        candidate,
        run_id="historic-run",
        trace=[{
            "filter_name": "signal_batch_limit",
            "stage": "paper_trader_selection",
            "decision": "passed",
            "reason_code": "signal_batch_limit",
            "inputs": {
                "trade_score": candidate.get("trade_score"),
                "symbol": candidate.get("symbol"),
                "direction": candidate.get("direction"),
            },
            "parameters": {"limit": 3},
        }],
    )

    monkeypatch.setattr(
        filter_evidence, "record_candidate_filter_trace", lambda *_args, **_kwargs: None
    )

    def fail_current_batch(*_args, **_kwargs):
        raise RuntimeError("forced current-run batch outage")

    monkeypatch.setattr(
        filter_evidence, "record_filter_decision_batch", fail_current_batch
    )

    result = asyncio.run(
        run_paper_trader(permission_mode="Paper", run_id="current-run-batch-outage")
    )

    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' "
        "AND opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    current_batches = get_db().execute(
        "SELECT COUNT(*) FROM filter_decision_batches WHERE run_id=?",
        ("current-run-batch-outage",),
    ).fetchone()[0]
    assert get_opportunity(opportunity.id)["status"] == "open", result
    assert pending_count == 0
    assert current_batches == 0
    assert get_opportunity(opportunity.id)["last_ranked_at"] is None
    assert any(
        error.startswith("accepted_filter_evidence_failed:")
        for error in result["errors"]
    )


def test_stale_permanent_rejection_cannot_overwrite_queued_opportunity(
    tmp_path, monkeypatch
):
    """A stale reject plan must lose to a completed open→queued claim."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.paper_trader import _finalize_permanent_skip
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="queued-before-stale-reject-finishes",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="winning-queue-claim",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    stale_candidate = {
        "opportunity_id": opportunity.id,
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "filter_trace": [{
            "filter_name": "symbol_filter",
            "stage": "paper_ranker",
            "decision": "rejected",
            "reason_code": "symbol_not_allowed",
            "inputs": {"symbol": opportunity.symbol},
            "parameters": {"mode": "whitelist"},
        }],
    }

    finalized = _finalize_permanent_skip(
        stale_candidate,
        terminal_status="reviewed",
        run_id="stale-reject-after-queue",
    )

    rejection_count = get_db().execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=? "
        "AND filter_name='symbol_filter' AND decision='rejected'",
        (opportunity.id,),
    ).fetchone()[0]
    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    assert finalized is False
    assert get_opportunity(opportunity.id)["status"] == "queued"
    assert pending_count == 1
    assert rejection_count == 0


def test_candidate_builder_overfetches_past_skipped_front_page():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [
        _make_opp(
            oid=f"skipped-{i}",
            symbol="ETH/USDT",
            confidence="speculative",
            confidence_score=20,
        )
        for i in range(10)
    ]
    opps.append(_make_opp(oid="executable-1", symbol="BTC/USDT", confidence="high", confidence_score=90))

    def list_page(*, limit):
        return opps[:limit]

    cfg = _mock_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"

    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_open_trade_opportunities", side_effect=list_page) as list_mock, \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "active",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }), \
         patch("aletheia.agents.paper_trader_ranker._evaluate_guards_safe", return_value=None):
        candidates = build_paper_candidates(limit=10)

    best = select_best_candidate(candidates)

    assert list_mock.call_args.kwargs["limit"] > 10
    assert len(candidates) <= 10
    assert best is not None
    assert best["opportunity_id"] == "executable-1"


def test_open_candidate_is_not_buried_by_reviewed_history(tmp_path, monkeypatch):
    """The ranker must query its open working set before applying its scan cap."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity

    open_opp = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        status="open",
        scanner_family="momentum",
    )
    insert_opportunity(open_opp)
    for index in range(501):
        insert_opportunity(Opportunity(
            symbol=f"REVIEWED{index}/USDT",
            market="spot",
            direction_bias="long",
            confidence="high",
            confidence_score=90,
            status="reviewed",
            scanner_family="momentum",
        ))

    cfg = _mock_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.universe.scanner_regime_scoring = {}
    factor = {
        "factor_key": "test",
        "status": "active",
        "authority_applied": False,
        "score_delta": 0.0,
        "reason": "test",
    }

    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value=factor), \
         patch("aletheia.agents.paper_trader_ranker._evaluate_guards_safe", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker._lesson_penalty", return_value=0.0), \
         patch("aletheia.agents.paper_trader_ranker._memo_scoring", return_value=0.0):
        candidates = build_paper_candidates(limit=50)

    assert [row["opportunity_id"] for row in candidates] == [open_opp.id]


# ---------------------------------------------------------------------------
# Regime scoring (Position C: strategy-evidence gate/scoring removed — the
# rules engine is deleted. Regime affinity survives as a scanner-family signal.)
# ---------------------------------------------------------------------------

def test_regime_scoring_is_direction_aware():
    """Bear regime should reward shorts and penalize longs; bull should do the opposite."""
    from unittest.mock import patch
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    bear = MarketRegime(state="bear", confidence=0.7, long_bias=0.25, short_bias=0.75)
    bull = MarketRegime(state="bull", confidence=0.7, long_bias=0.75, short_bias=0.25)
    factor_signal = {"status": "active"}
    opp = {"confidence_score": 80}

    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=bear):
        score_bear_short = _compute_trade_score(opp, None, factor_signal, "high", None, direction="short")
        score_bear_long = _compute_trade_score(opp, None, factor_signal, "high", None, direction="long")

    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=bull):
        score_bull_long = _compute_trade_score(opp, None, factor_signal, "high", None, direction="long")
        score_bull_short = _compute_trade_score(opp, None, factor_signal, "high", None, direction="short")

    # Bear: shorts score higher than longs (regime smooth: gap < old discrete 35pt but direction preserved)
    assert score_bear_short > score_bear_long
    assert score_bear_short - score_bear_long > 10  # meaningful gap, not just noise

    # Bull: longs score higher than shorts (gap < old discrete 30pt but direction preserved)
    assert score_bull_long > score_bull_short
    assert score_bull_long - score_bull_short > 10


def test_family_direction_bias_config_override_reaches_score():
    """An operator override in cfg.universe.family_direction_bias must change the
    ranker score (regression for the half-active config channel — declaring the
    schema field is what unlocks the override; before that, pydantic silently
    dropped the key and the ranker always used the hardcoded defaults).

    short_bias:short default is -14.0; overriding to -25.0 must lower the score
    by exactly 11.0 (the merge is override-on-default, additive in the delta).
    """
    from unittest.mock import patch
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.config.schema import AletheiaConfig
    from aletheia.regime.detector import MarketRegime

    # Neutral regime: sideways at confidence 0.3 (< 0.6 threshold) applies NO
    # regime delta, isolating the family_direction_bias term. Both arms share
    # this regime so any regime-derived term cancels in the subtraction.
    neutral = MarketRegime(state="sideways", confidence=0.3, long_bias=0.5, short_bias=0.5)

    # Symbol with no closed-position history → _symbol_edge_score returns 0.0
    # (n < 3 guard). Same for both arms, so it cancels in the subtraction.
    opp = {"symbol": "NOHIST/USDT", "confidence_score": 50, "scanner_family": "short_bias"}
    factor = {"status": "watch"}

    baseline_cfg = AletheiaConfig()
    assert baseline_cfg.universe.family_direction_bias == {}  # no-op default

    override_cfg = AletheiaConfig()
    override_cfg.universe.family_direction_bias = {"short_bias:short": -25.0}

    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=neutral), \
         patch("aletheia.agents.paper_trader_ranker._lesson_penalty", return_value=0.0), \
         patch("aletheia.agents.paper_trader_ranker._memo_scoring", return_value=0.0):
        # _family_direction_bias() and _scanner_regime_scoring() both do a local
        # `from aletheia.config.loader import load_config`, so patch the source
        # module to inject the config under test.
        with patch("aletheia.config.loader.load_config", return_value=baseline_cfg):
            s_baseline = _compute_trade_score(opp, None, factor, "medium", None, direction="short")
        with patch("aletheia.config.loader.load_config", return_value=override_cfg):
            s_override = _compute_trade_score(opp, None, factor, "medium", None, direction="short")

    # Override is MORE negative by exactly the delta (default -14 → override -25 = -11).
    assert abs((s_override - s_baseline) - (-11.0)) < 0.01, (
        f"family_direction_bias override did not propagate to the score: "
        f"baseline={s_baseline} override={s_override} "
        f"(expected delta -11.0 from short_bias:short -14 → -25)"
    )


# ---------------------------------------------------------------------------
# Risk-mirror pre-filters (patch 4, 2026-07-31): proposals that the risk engine
# would reject are filtered at the ranker stage with a durable skip_reason —
# symbol whitelist, demo position capacity, same-symbol open, per-symbol
# notional accumulation.
# ---------------------------------------------------------------------------

def _pos(symbol, direction="long", qty=1.0, entry=100.0, mode="bitget_demo"):
    return {
        "symbol": symbol, "direction": direction, "quantity": qty,
        "entry_price": entry, "current_price": entry, "execution_mode": mode,
    }


def test_symbol_filter_reads_risk_symbol_list():
    """The ranker's whitelist must mirror the risk engine's check #1, which
    reads cfg.risk.symbol_list — not the (empty) exchange-level list."""
    from unittest.mock import patch
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    cfg = _mock_config()
    cfg.risk.symbol_list = ["ETH/USDT"]  # BTC deliberately outside the whitelist

    opps = [
        _make_opp(oid="allowed-1", symbol="ETH/USDT", confidence="high", confidence_score=90),
        _make_opp(oid="blocked-1", symbol="BTC/USDT", confidence="high", confidence_score=95),
    ]
    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_open_trade_opportunities", return_value=opps), \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.list_all_open_positions", return_value=[]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "active",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }):
        candidates = build_paper_candidates(limit=50)
    by_oid = {c["opportunity_id"]: c for c in candidates}
    assert by_oid["allowed-1"]["skip_reason"] is None
    assert by_oid["blocked-1"]["skip_reason"] == "symbol_not_allowed:whitelist"
    assert select_best_candidate(candidates)["opportunity_id"] == "allowed-1"


def test_position_capacity_prefilter_skips_when_demo_pool_full():
    """Mirror of risk check #6: with the demo pool at max_open_positions, all
    candidates get a durable max_demo_positions_reached skip instead of a risk
    rejection."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    open_positions = [_pos(f"SYM{i}/USDT") for i in range(15)]  # 15 == demo cap
    opps = [_make_opp(oid="opp-cap", symbol="ETH/USDT", confidence="high")]
    with _patch_deps(opps, factor_status="active", open_positions=open_positions):
        candidates = build_paper_candidates(limit=50)
    assert candidates[0]["skip_reason"].startswith("max_demo_positions_reached:")


def test_symbol_already_open_prefilter_skips_duplicate_symbol():
    """Mirror of risk check #6b: a candidate on a symbol that already has an
    open demo position is skipped at the ranker stage."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    open_positions = [_pos("ETH/USDT", qty=0.5, entry=3000.0)]
    opps = [_make_opp(oid="opp-dup", symbol="ETH/USDT", confidence="high")]
    with _patch_deps(opps, factor_status="active", open_positions=open_positions):
        candidates = build_paper_candidates(limit=50)
    assert candidates[0]["skip_reason"] == "symbol_already_open:ETH/USDT"


def test_prefilters_pass_when_portfolio_has_room():
    """With an empty-ish book the new pre-filters must not block candidates."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    open_positions = [_pos("SOL/USDT")]  # 1 open, under cap, different symbol
    opps = [_make_opp(oid="opp-ok", symbol="ETH/USDT", confidence="high", confidence_score=90)]
    with _patch_deps(opps, factor_status="active", open_positions=open_positions):
        candidates = build_paper_candidates(limit=50)
        best = select_best_candidate(candidates)
    assert best is not None
    assert best["opportunity_id"] == "opp-ok"
    assert best["skip_reason"] is None
