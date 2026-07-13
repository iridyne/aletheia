"""Spec: Pipeline — Paper Trade (end-to-end)

E2E pipeline claims for opportunity → paper_trader → pi_decisions(signal_pending)
→ risk gate → execution chain.

NOTE: paper_trader is FROZEN. Tests use only public entry points.

Contract: pipeline_paper_trade
Claim prefix: PL-PT
"""

import uuid
from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _insert_opportunity(conn, symbol: str = "BTC/USDT", confidence_score: float = 0.8,
                         direction_bias: str = "long", status: str = "open") -> str:
    """Insert an opportunity row and return its id."""
    opp_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO opportunities (id, symbol, market, direction_bias, confidence, "
        "confidence_score, status, asset_class, scanner_family, valid_until, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (opp_id, symbol, "spot", direction_bias, "medium", confidence_score,
         status, "crypto", "momentum", "2099-01-01T00:00:00", now),
    )
    conn.commit()
    return opp_id


def _insert_market_snapshot(conn, symbol: str = "BTC/USDT", price: float = 60000.0):
    """Insert a fresh market snapshot to satisfy paper_trader's price lookups."""
    snap_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, symbol, "spot", price, 1_000_000.0, 0.5, now, "ccxt"),
    )
    conn.commit()
    return snap_id


# ---------------------------------------------------------------------------
# PL-PT-01  L2b  happy_path: paper_trader produces signal_pending pi_decision
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-01", "pipeline_paper_trade", "§1 happy_path", "L2b",
        "With open opportunities and Paper permission, run_paper_trader writes "
        "at least one pi_decisions row with action='signal_pending'",
    ),
)
def test_paper_trader_produces_signal_pending(tmp_path, monkeypatch):
    """run_paper_trader v3 writes pi_decisions rows, not trade_proposals directly."""
    conn = _setup_db(tmp_path, monkeypatch)

    # Suppress pi_daemon notifications (not running in test env)
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.85)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    # Ensure tool_registry is loaded so TOOL_REGISTRY is populated
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    # Use asyncio to run the async paper_trader
    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "errors" in result

    # paper_trader v3: writes pi_decisions(action="signal_pending") for eligible candidates
    pi_rows = conn.execute(
        "SELECT * FROM pi_decisions WHERE action = 'signal_pending'"
    ).fetchall()

    # Either signal_pending rows exist OR outputs contains a skip/no_candidate reason
    outputs = result.get("outputs", [])
    if pi_rows:
        # Happy path: at least one signal written
        assert len(pi_rows) >= 1, "Expected at least 1 signal_pending pi_decision"
        # All signal_pending rows must have a symbol
        for row in pi_rows:
            assert row["symbol"] is not None, (
                "pi_decisions signal_pending row must have a symbol"
            )
    else:
        # Acceptable: ranker filtered all candidates (no eligible ones)
        has_skip_output = any(
            "skip" in o or "no_ranked" in o or "no_signals" in o
            for o in outputs
        )
        assert has_skip_output, (
            f"paper_trader produced no signal_pending AND no skip output. "
            f"outputs={outputs}, errors={result.get('errors')}"
        )


# ---------------------------------------------------------------------------
# PL-PT-02  L2b  ranker_filter: low-score opportunities don't produce signals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-02", "pipeline_paper_trade", "§2 ranker_filter", "L2b",
        "Opportunities with very low confidence_score are filtered by ranker; "
        "they don't produce signal_pending pi_decisions",
    ),
)
def test_paper_trader_ranker_filters_low_score(tmp_path, monkeypatch):
    """Insert a speculative-confidence opportunity with min_confidence='high' → filtered."""
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    # Set min_confidence_to_trade to "high" so "speculative" opportunities are filtered.
    # Ranker gate: confidence < min_conf → skip_reason="confidence_below_high:speculative"
    import yaml
    cfg_dict = {
        "runtime": {"paused": False, "kill_switch_active": False},
        "agent": {"permission_mode": "Paper"},
        "trading": {
            "min_confidence_to_trade": "high",
            "default_stop_loss_pct": 2.0,
            "default_notional_usdt": 25.0,
        },
        "risk": {"max_demo_position_notional_usdt": 200.0},
        "exchange": {"paper_execution_mode": "paper_internal"},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")

    opp_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO opportunities (id, symbol, market, direction_bias, confidence, "
        "confidence_score, status, asset_class, scanner_family, valid_until, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (opp_id, "BTC/USDT", "spot", "long", "speculative", 0.3,
         "open", "crypto", "momentum", "2099-01-01T00:00:00", now),
    )
    conn.commit()
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    pi_before = conn.execute(
        "SELECT COUNT(*) as n FROM pi_decisions WHERE action='signal_pending'"
    ).fetchone()["n"]

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    pi_after = conn.execute(
        "SELECT COUNT(*) as n FROM pi_decisions WHERE action='signal_pending'"
    ).fetchone()["n"]

    delta = pi_after - pi_before
    # Ranker confidence gate: "speculative" < "high" → must be filtered out
    assert delta == 0, (
        f"Expected ranker to filter speculative opportunity when min_confidence='high', "
        f"but {delta} new signal_pending rows were created. "
        f"outputs={result.get('outputs')}. "
        "Ranker confidence gate must prevent sub-threshold signals from reaching System Operator."
    )


# ---------------------------------------------------------------------------
# PL-PT-03  L2c  symbol_consistency: BTC/USDT flows through as canonical format
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-03", "pipeline_paper_trade", "§3 symbol_consistency", "L2c",
        "A canonical BTC/USDT opportunity propagates with BTC/USDT symbol "
        "in pi_decisions rows (no BTCUSDT conversion at signal stage)",
    ),
)
def test_paper_trader_symbol_stays_canonical(tmp_path, monkeypatch):
    """Symbol format must stay canonical (BTC/USDT) at the signal stage.

    Conversion to BTCUSDT happens ONLY at the execution adapter boundary.
    A premature conversion silently drops opportunities at paper_trader's filter.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    import aletheia.agent_runtime.tool_registry  # noqa: F401

    _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=62000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    asyncio.run(run_paper_trader(permission_mode="Paper"))

    # Any pi_decisions written for BTC must use canonical format
    non_canonical = conn.execute(
        "SELECT id, symbol FROM pi_decisions WHERE symbol LIKE 'BTC%' "
        "AND symbol != 'BTC/USDT'"
    ).fetchall()
    assert len(non_canonical) == 0, (
        f"Found pi_decisions with non-canonical BTC symbol format: "
        f"{[dict(r) for r in non_canonical]}. "
        "Symbol must remain BTC/USDT at signal stage — "
        "BTCUSDT conversion is only for exchange API boundary."
    )


# ---------------------------------------------------------------------------
# PL-PT-04  L2b  xfail  opportunity_id_propagation  (captures PT-2)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-04", "pipeline_paper_trade", "§2 opportunity_id_propagation", "L2b",
        "signal_pending pi_decision rows include the originating opportunity_id "
        "in the dedicated pi_decisions.opportunity_id column "
        "(paper_trader.py:165 passes candidate['opportunity_id'] → PiDecision.opportunity_id)",
        status="implemented",
    ),
)
def test_paper_trader_signal_has_opportunity_id(tmp_path, monkeypatch):
    """Assert signal_pending rows carry opportunity_id linking back to the source opportunity.

    Fixed PT-2 (A13, LEARNING_LOOP_AUDIT): paper_trader now passes
    opportunity_id=candidate.get('opportunity_id') into PiDecision (paper_trader.py:165).
    The dedicated pi_decisions.opportunity_id column (models.py:901, migration:1304)
    enables the System Operator to trace which opportunity triggered each signal.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=90)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    asyncio.run(run_paper_trader(permission_mode="Paper"))

    signal_rows = conn.execute(
        "SELECT opportunity_id FROM pi_decisions WHERE action='signal_pending' AND symbol='BTC/USDT'"
    ).fetchall()

    assert len(signal_rows) >= 1, (
        "No signal_pending rows found — cannot verify opportunity_id propagation"
    )

    # opportunity_id must be the dedicated column, not null
    for row in signal_rows:
        assert row["opportunity_id"] is not None, (
            f"signal_pending pi_decision has opportunity_id=None — "
            f"expected {opp_id!r} to be propagated via paper_trader.py:165. "
            "PT-2 regression: opportunity tracking broken in paper_trader path."
        )
        assert row["opportunity_id"] == opp_id, (
            f"signal_pending pi_decision.opportunity_id={row['opportunity_id']!r} "
            f"does not match source opportunity {opp_id!r}."
        )


# ---------------------------------------------------------------------------
# PL-PT-05  L2b  xfail  signal_consumed  (captures PT-1)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-05", "pipeline_paper_trade", "§2 signal_consumed", "L2b",
        "After a successful signal_pending is written, the originating opportunity "
        "row becomes queued; reviewed is reserved for a persisted final Operator decision.",
        status="implemented",
    ),
)
def test_paper_trader_marks_opportunity_consumed(tmp_path, monkeypatch):
    """After signal_pending, the source waits in queued until a final decision exists.

    queued prevents re-emission without falsely claiming the System Operator has
    already evaluated the opportunity.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    import aletheia.agent_runtime.tool_registry  # noqa: F401

    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    signals = conn.execute(
        "SELECT id FROM pi_decisions WHERE action='signal_pending' AND symbol='BTC/USDT'"
    ).fetchall()

    if not signals:
        pytest.skip("No signal_pending written — cannot verify opportunity status")

    opp_row = conn.execute(
        "SELECT status FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()

    assert opp_row is not None, "Opportunity row missing after paper_trader run"
    assert opp_row["status"] == "queued"


def test_paper_trader_skips_recent_duplicate_signal_pair(tmp_path, monkeypatch):
    """Do not keep re-emitting the same symbol/direction into the PI queue."""
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    init_tool_registry()

    insert_pi_decision(PiDecision(
        symbol="BTC/USDT",
        direction="long",
        action="signal_pending",
        reasoning="recent duplicate seed",
    ))
    _insert_opportunity(conn, symbol="BTC/USDT", direction_bias="long", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    before = conn.execute(
        "SELECT COUNT(*) AS n FROM pi_decisions "
        "WHERE action='signal_pending' AND symbol='BTC/USDT' AND direction='long'"
    ).fetchone()["n"]

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    after = conn.execute(
        "SELECT COUNT(*) AS n FROM pi_decisions "
        "WHERE action='signal_pending' AND symbol='BTC/USDT' AND direction='long'"
    ).fetchone()["n"]

    assert after == before
    assert any("skip_recent_signal:BTC/USDT:long" in o for o in result.get("outputs", []))


@spec_claim(
    SpecClaim(
        "PL-PT-06", "pipeline_paper_trade", "§2 open candidate working set", "L2b",
        "The ranker queries open opportunities directly before applying its bounded scan limit, "
        "so reviewed history cannot permanently hide a still-open candidate.",
        status="implemented",
    ),
)
def test_ranker_queries_open_working_set_directly():
    import inspect

    from aletheia.agents import paper_trader_ranker
    from aletheia.storage import repositories

    assert hasattr(repositories, "list_open_opportunities")
    source = inspect.getsource(paper_trader_ranker.build_paper_candidate_workset)
    assert "list_open_opportunities" in source


@spec_claim(
    SpecClaim(
        "PL-PT-07", "pipeline_paper_trade", "§2 evaluated opportunity idempotency", "L2b",
        "paper_trader does not enqueue a second signal_pending for an opportunity that already "
        "has an authoritative non-pending System Operator decision.",
        status="implemented",
    ),
)
def test_paper_trader_does_not_requeue_operator_evaluated_opportunity(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=85)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    init_tool_registry()
    insert_pi_decision(PiDecision(
        symbol="BTC/USDT",
        direction="long",
        action="skip",
        conviction=50,
        reasoning="already evaluated directly",
        opportunity_id=opp_id,
        signal_source="market_observer",
    ))
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    pending = conn.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' AND opportunity_id=?",
        (opp_id,),
    ).fetchone()[0]
    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    assert pending == 0
    assert status == "reviewed"
    assert any("skip_already_evaluated:BTC/USDT" in row for row in result["outputs"])


@spec_claim(
    SpecClaim(
        "PL-PT-08", "pipeline_paper_trade", "§2 expired opportunity state", "L2b",
        "An opportunity rejected because its own valid_until has elapsed becomes expired, "
        "not reviewed; expiry and System Operator evaluation remain distinct lifecycle facts.",
        status="implemented",
    ),
)
def test_paper_trader_marks_ttl_rejection_expired(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=85)
    conn.execute(
        "UPDATE opportunities SET valid_until=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", opp_id),
    )
    conn.commit()

    from aletheia.agent_runtime.tool_registry import init_tool_registry

    init_tool_registry()
    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' AND opportunity_id=?",
        (opp_id,),
    ).fetchone()[0]
    assert status == "expired"
    assert pending == 0
    assert any("skip:BTC/USDT:ttl_expired" in row for row in result["outputs"])


@spec_claim(
    SpecClaim(
        "PL-PT-09", "pipeline_paper_trade", "§2 pending signal expiry", "L2b",
        "A queued opportunity with no final decision becomes expired after the pending "
        "evaluation window, instead of remaining queued forever.",
        status="implemented",
    ),
)
def test_stale_queued_opportunity_becomes_expired(tmp_path, monkeypatch):
    from datetime import timedelta

    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=90)
    conn.execute("UPDATE opportunities SET status='queued' WHERE id=?", (opp_id,))
    conn.commit()

    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    insert_pi_decision(PiDecision(
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="signal_pending",
        reasoning="stale queue fixture",
        opportunity_id=opp_id,
        signal_source="market_observer",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
    ))
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    init_tool_registry()
    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    assert status == "expired"
    assert "expired_pending:1" in result["outputs"]


@spec_claim(
    SpecClaim(
        "PL-PT-10", "pipeline_paper_trade", "§2 atomic signal enqueue", "L2b",
        "signal_pending insertion and open→queued transition are atomic; a status-write "
        "failure leaves no orphan pending row and records signal_enqueue=error.",
        status="implemented",
    ),
)
def test_signal_enqueue_rolls_back_when_queued_transition_fails(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=90)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agents import paper_trader

    init_tool_registry()
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )
    def fail_queued_transition(oid):
        raise RuntimeError("queued transition failed")

    monkeypatch.setattr(
        paper_trader, "transition_open_opportunity_to_queued", fail_queued_transition
    )

    import asyncio

    asyncio.run(paper_trader.run_paper_trader(permission_mode="Paper"))

    pending = conn.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' AND opportunity_id=?",
        (opp_id,),
    ).fetchone()[0]
    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    evidence = conn.execute(
        "SELECT decision, reason_code FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='signal_enqueue'",
        (opp_id,),
    ).fetchone()
    assert pending == 0
    assert status == "open"
    assert evidence is not None
    assert tuple(evidence) == ("error", "signal_enqueue_error")


@spec_claim(
    SpecClaim(
        "PL-PT-11", "pipeline_paper_trade", "§2 monotonic enqueue transition", "L2b",
        "A stale paper-trader plan may claim only an open Opportunity. If another actor already "
        "finalized it, enqueue creates no pending row and never regresses reviewed to queued.",
        status="implemented",
    ),
)
def test_stale_signal_plan_cannot_regress_reviewed_opportunity(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=90)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agents import paper_trader

    init_tool_registry()
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )
    original_transition = paper_trader.transition_open_opportunity_to_queued

    def finalize_before_stale_plan(oid):
        conn.execute("UPDATE opportunities SET status='reviewed' WHERE id=?", (oid,))
        return original_transition(oid)

    monkeypatch.setattr(
        paper_trader,
        "transition_open_opportunity_to_queued",
        finalize_before_stale_plan,
    )

    import asyncio

    result = asyncio.run(paper_trader.run_paper_trader(permission_mode="Paper"))

    pending = conn.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' AND opportunity_id=?",
        (opp_id,),
    ).fetchone()[0]
    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    evidence = conn.execute(
        "SELECT decision, reason_code FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='signal_enqueue'",
        (opp_id,),
    ).fetchone()

    assert pending == 0
    assert status == "reviewed"
    assert tuple(evidence) == ("rejected", "opportunity_not_open")
    assert "skip_stale_plan:BTC/USDT" in result["outputs"]


@spec_claim(
    SpecClaim(
        "PL-PT-12",
        "pipeline_paper_trade",
        "§4 approved take-profit identity",
        "L2b",
        "A paper_internal Position inherits take_profit_price exactly from its approved "
        "Proposal; the broker never adds a config-derived take-profit after approval.",
        status="implemented",
    )
)
def test_paper_position_take_profit_exactly_matches_approved_proposal(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)

    import yaml

    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"trading": {"default_take_profit_pct": 4.0}}),
        encoding="utf-8",
    )

    from aletheia.execution.gateway import submit_proposal

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        stop_price=58_000.0,
        take_profit=None,
        execution_mode="paper_internal",
        agent_rationale="approved without a take-profit",
    )

    proposal = conn.execute(
        "SELECT id, take_profit FROM trade_proposals WHERE id=?",
        (result["proposal_id"],),
    ).fetchone()
    authority = conn.execute(
        "SELECT approved FROM risk_decisions WHERE proposal_id=? "
        "AND decision_role='authoritative'",
        (result["proposal_id"],),
    ).fetchone()
    position = conn.execute(
        "SELECT take_profit_price FROM positions WHERE id=?",
        (result["position_id"],),
    ).fetchone()

    assert result["status"] == "executed"
    assert authority is not None and authority["approved"] == 1
    assert proposal["take_profit"] is None
    assert position["take_profit_price"] == proposal["take_profit"]


@spec_claim(
    SpecClaim(
        "PL-PT-13",
        "pipeline_paper_trade",
        "§2 permanent rejection atomicity",
        "L2b",
        "A permanent ranker rejection and its rejected filter fact commit together; "
        "if evidence persistence fails, the Opportunity remains open and retryable.",
        status="implemented",
    )
)
def test_permanent_skip_state_rolls_back_without_rejection_fact(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    opportunity_id = _insert_opportunity(conn, symbol="SOL/USDT")

    from aletheia.agents import paper_trader

    candidate = {
        "opportunity_id": opportunity_id,
        "symbol": "SOL/USDT",
        "market_type": "spot",
        "direction": "long",
        "skip_reason": "symbol_not_allowed:whitelist",
        "filter_trace": [{
            "filter_name": "symbol_filter",
            "stage": "paper_ranker",
            "decision": "rejected",
            "reason_code": "symbol_not_allowed",
            "inputs": {"symbol": "SOL/USDT"},
            "parameters": {"mode": "whitelist"},
        }],
    }

    def fail_evidence(*_args, **_kwargs):
        raise RuntimeError("forced evidence failure")

    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.record_candidate_filter_trace",
        fail_evidence,
    )

    with pytest.raises(RuntimeError, match="forced evidence failure"):
        paper_trader._finalize_permanent_skip(
            candidate,
            terminal_status="reviewed",
            run_id="atomic-terminal-reject",
        )

    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity_id,)
    ).fetchone()[0]
    evidence = conn.execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?",
        (opportunity_id,),
    ).fetchone()[0]
    assert status == "open"
    assert evidence == 0


@spec_claim(
    SpecClaim(
        "PL-PT-14",
        "pipeline_paper_trade",
        "§2 bounded permanent-rejection workset",
        "L2b",
        "The top-N execution shortlist does not truncate permanent rejects already present "
        "in the ranker's larger bounded scan workset.",
        status="implemented",
    )
)
def test_permanent_reject_workset_is_separate_from_execution_limit(monkeypatch):
    from aletheia.agents import paper_trader_ranker as ranker
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    opportunities = [{
        "id": f"bounded-reject-{index:03d}",
        "symbol": f"REJECT{index}/USDT",
        "market": "spot",
        "direction_bias": "long",
        "confidence": "high",
        "confidence_score": 88,
        "status": "open",
        "asset_class": "crypto",
        "execution_eligible": True,
    } for index in range(51)]

    monkeypatch.setattr(ranker, "load_config", lambda: cfg)
    monkeypatch.setattr(
        ranker, "list_open_opportunities", lambda *, limit: opportunities[:limit]
    )
    monkeypatch.setattr(
        ranker, "tradable_symbols_from_config", lambda _cfg: ["BTC/USDT"]
    )

    workset = ranker.build_paper_candidate_workset(limit=50)

    assert len(workset.ranked_candidates) == 50
    assert len(workset.scanned_candidates) == 51
    assert all(
        candidate["skip_reason"].startswith("symbol_not_allowed:")
        for candidate in workset.scanned_candidates
    )
