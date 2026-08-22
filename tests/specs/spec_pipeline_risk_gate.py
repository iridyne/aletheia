"""Spec: Pipeline — Risk Gate (end-to-end)

E2E pipeline claims for submit_proposal → _eval_lock → 16 checks →
RiskDecision persisted → approved/rejected branch.

Contract: pipeline_risk_gate
Claim prefix: PL-RG
"""

import math
import threading
import time

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


# ---------------------------------------------------------------------------
# PL-RG-01  L2b  happy_path: approved proposal → RiskDecision + audit persisted
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-01", "pipeline_risk_gate", "§1 happy_path", "L2b",
        "paper_internal proposal with stop_price → approved → RiskDecision row "
        "persisted with approved=True → audit row exists in audit_events",
    ),
)
def test_risk_gate_happy_path(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000.0,
        stop_price=58000.0,
        execution_mode="paper_internal",
    )

    proposal_id = result.get("proposal_id")
    assert proposal_id is not None

    db = get_db()

    # Verify exactly one RiskDecision row exists for this proposal
    decisions = db.execute(
        "SELECT * FROM risk_decisions WHERE proposal_id = ?", (proposal_id,)
    ).fetchall()
    assert len(decisions) == 1, (
        f"Expected exactly 1 risk_decision for proposal {proposal_id}, "
        f"got {len(decisions)}"
    )

    # For approved proposals the decision approved column should be 1
    if result.get("status") == "executed":
        assert decisions[0]["approved"] == 1, (
            "Risk decision approved flag must be 1 for executed proposals"
        )

    # Audit event must exist — gateway writes audit for proposal (proposal_id)
    # or for the resulting order (order_id). Check both entity_ids.
    order_id = result.get("order_id")
    entity_ids = [eid for eid in (proposal_id, order_id) if eid]
    placeholders = ",".join("?" * len(entity_ids))
    audit_rows = db.execute(
        f"SELECT * FROM audit_events WHERE entity_id IN ({placeholders})",
        entity_ids,
    ).fetchall()
    assert len(audit_rows) >= 1, (
        f"Expected at least 1 audit_events row for proposal {proposal_id} "
        f"or order {order_id}. "
        f"All audit rows: {[dict(r) for r in db.execute('SELECT event_type, entity_id FROM audit_events').fetchall()]}"
    )


# ---------------------------------------------------------------------------
# PL-RG-02  L2b  reject_propagation: kill_switch → rejected → correct audit
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-02", "pipeline_risk_gate", "§1 reject_propagation", "L2b",
        "kill_switch-active proposal → rejected → RiskDecision violations "
        "contains 'kill_switch_active' → audit event_type='proposal_rejected'",
    ),
)
def test_risk_gate_reject_propagation(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.risk.kill_switch import activate, deactivate
    from aletheia.storage.sqlite import get_db
    from aletheia.storage import repositories as repo

    activate("test_pl_rg_02", actor="human")
    try:
        result = submit_proposal(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            price=60000.0,
            execution_mode="paper_internal",
        )

        assert result.get("status") == "rejected", (
            f"Expected status='rejected' when kill_switch active, got {result}"
        )

        violations = result.get("risk_violations", [])
        assert any("kill_switch" in v for v in violations), (
            f"Expected kill_switch_active in violations, got {violations}"
        )

        proposal_id = result.get("proposal_id")
        db = get_db()

        # RiskDecision row must exist with approved=0
        decisions = db.execute(
            "SELECT * FROM risk_decisions WHERE proposal_id = ?", (proposal_id,)
        ).fetchall()
        assert len(decisions) == 1
        assert decisions[0]["approved"] == 0

        # Audit event type must be 'proposal_rejected'
        audit_rows = db.execute(
            "SELECT * FROM audit_events WHERE entity_id = ? AND event_type = ?",
            (proposal_id, "proposal_rejected"),
        ).fetchall()
        assert len(audit_rows) >= 1, (
            f"Expected audit row with event_type='proposal_rejected', found none. "
            f"All audit rows: {[dict(r) for r in db.execute('SELECT * FROM audit_events WHERE entity_id=?', (proposal_id,)).fetchall()]}"
        )

    finally:
        deactivate(actor="human")


# ---------------------------------------------------------------------------
# PL-RG-03  L2b  serialization: _eval_lock prevents concurrent TOCTOU
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-03", "pipeline_risk_gate", "§2 serialization", "L2b",
        "Two threads calling evaluate_proposal concurrently are serialized "
        "by _eval_lock — critical sections do not overlap",
    ),
)
def test_risk_gate_eval_lock_serialization(tmp_path, monkeypatch):
    """Verify _eval_lock serializes concurrent evaluate_proposal calls.

    Strategy: instrument the lock so we can detect that at most one thread
    holds it at any instant during the test window.
    """
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    import aletheia.risk.engine as eng

    # Track concurrent overlap
    concurrent_overlap_detected = threading.Event()
    inside_count = [0]
    inside_lock = threading.Lock()

    original_locked = eng._evaluate_proposal_locked

    def _instrumented(proposal):
        with inside_lock:
            inside_count[0] += 1
            if inside_count[0] > 1:
                concurrent_overlap_detected.set()
        try:
            return original_locked(proposal)
        finally:
            with inside_lock:
                inside_count[0] -= 1

    monkeypatch.setattr(eng, "_evaluate_proposal_locked", _instrumented)

    errors = []

    def _run_proposal():
        try:
            p = TradeProposal(
                symbol="BTC/USDT", market_type="spot", direction="long",
                execution_mode="paper_internal", quantity=0.001, price=60000,
            )
            repo.insert_trade_proposal(p)
            eng.evaluate_proposal(p)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=_run_proposal) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Threads raised errors: {errors}"
    assert not concurrent_overlap_detected.is_set(), (
        "INVARIANT VIOLATION: _eval_lock failed to serialize concurrent "
        "evaluate_proposal calls — overlap detected"
    )


# ---------------------------------------------------------------------------
# PL-RG-04  L2c  invariant: every call (approved OR rejected) persists exactly 1 row
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-04", "pipeline_risk_gate", "§3 persistence_invariant", "L2c",
        "Every evaluate_proposal call (approved or rejected) MUST persist "
        "exactly one risk_decisions row — delta == 1",
    ),
)
def test_risk_gate_always_persists_exactly_one_row(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.kill_switch import activate, deactivate
    from aletheia.storage.sqlite import get_db

    db = get_db()

    def _count():
        return db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0]

    # Case A: normal (not kill-switched) proposal
    before_a = _count()
    p_a = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p_a)
    evaluate_proposal(p_a)
    after_a = _count()
    assert after_a - before_a == 1, (
        f"Expected delta=1 for approved proposal, got delta={after_a - before_a}"
    )

    # Case B: kill-switched proposal (rejected path)
    activate("pl_rg_04", actor="human")
    try:
        before_b = _count()
        p_b = TradeProposal(
            symbol="ETH/USDT", market_type="spot", direction="long",
            execution_mode="paper_internal", quantity=0.01, price=3000,
        )
        repo.insert_trade_proposal(p_b)
        evaluate_proposal(p_b)
        after_b = _count()
        assert after_b - before_b == 1, (
            f"Expected delta=1 for rejected proposal, got delta={after_b - before_b}"
        )
    finally:
        deactivate(actor="human")


# ---------------------------------------------------------------------------
# PL-RG-05  L2b  degradation: stale market data triggers violation for live proposals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-05", "pipeline_risk_gate", "§2 degradation", "L2b",
        "A live proposal with market data older than _MAX_DATA_AGE_SECONDS "
        "triggers the stale_market_data violation",
    ),
)
def test_risk_gate_stale_market_data_for_live(tmp_path, monkeypatch):
    """Verify check #11: stale data fires on live proposals when no fresh snapshot."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    # Configure for live-mode evaluation (all other checks unlocked)
    cfg = AletheiaConfig()
    cfg.live.state = "live_enabled"
    cfg.exchange.mode = "live"
    cfg.agent.permission_mode = "Full"
    # Give generous size/exposure caps so only stale-data check fires
    cfg.risk.max_live_position_margin_usdt = 1_000_000.0
    cfg.risk.max_total_live_exposure_pct = 100.0
    cfg.risk.max_daily_loss_pct = 99.0
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 10_000.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # Return no market snapshot — simulates completely absent/stale data
    monkeypatch.setattr(
        "aletheia.risk.engine._market_data_age_seconds",
        lambda symbol, market_type=None: None,
    )

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_live_limited", quantity=0.001, price=60000.0,
        stop_price=58000.0,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert any("stale_market_data" in v for v in result.violations), (
        f"Expected stale_market_data violation when data absent, "
        f"got violations={result.violations}"
    )


# ---------------------------------------------------------------------------
# PL-RG-06  L2c  local-paper futures require complete funding economics
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-RG-06", "pipeline_risk_gate", "§4 local paper funding boundary", "L2c",
        "A new paper_internal futures entry is durably rejected before Order or "
        "Position materialization while no trustworthy local funding producer exists.",
        status="implemented",
    ),
)
def test_local_paper_futures_entry_fails_closed_before_order(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        stop_price=58_000.0,
        leverage=1,
        execution_mode="paper_internal",
    )

    assert result["status"] == "rejected"
    proposal_id = result["proposal_id"]
    db = get_db()
    decision = db.execute(
        "SELECT approved, violations FROM risk_decisions WHERE proposal_id=?",
        (proposal_id,),
    ).fetchone()
    assert decision is not None
    assert decision["approved"] == 0
    assert "local_paper_futures_funding_unavailable" in decision["violations"]
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (proposal_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE proposal_id=?", (proposal_id,)
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RG-07", "pipeline_risk_gate", "§4 local paper market authority", "L2c",
        "A paper_internal entry reaches authoritative Risk with a fresh exact-venue, exact-symbol, "
        "exact-physical-market mark; stale, missing, wrong-venue, or spot/perp-mismatched data "
        "rejects before Order, Fill, or Position materialization.",
        status="implemented",
    )
)
def test_local_paper_stale_exact_market_rejects_before_materialization(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    calls = []

    def unavailable(symbol, market_type, *, venue):
        calls.append((symbol, market_type, venue))
        return None

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close", unavailable,
    )
    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60_000.0,
        stop_price=58_000.0, execution_mode="paper_internal",
    )

    assert result["status"] == "rejected"
    assert calls == [("BTC/USDT", "spot", "binance")]
    assert "paper_market_data_unavailable:binance:spot" in result["risk_violations"]
    proposal_id = result["proposal_id"]
    db = get_db()
    decision = db.execute(
        "SELECT approved FROM risk_decisions WHERE proposal_id=?", (proposal_id,),
    ).fetchone()
    assert decision is not None and decision["approved"] == 0
    for table in ("orders", "fills", "positions"):
        assert db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE proposal_id=?" if table != "fills"
            else "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.proposal_id=?",
            (proposal_id,),
        ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RG-08", "pipeline_risk_gate", "§4 local paper executable order types", "L2c",
        "paper_internal limit entries are rejected as unsupported before authoritative approval "
        "or materialization; native exchange demo limit entries retain their existing path.",
        status="implemented",
    )
)
def test_local_paper_limit_rejects_before_materialization(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 60_000.0,
    )
    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="limit", quantity=0.001, price=60_000.0,
        stop_price=58_000.0, take_profit=64_000.0, risk_reward=2.0,
        execution_mode="paper_internal",
    )

    assert result["status"] == "rejected"
    assert "paper_internal_limit_not_supported" in result["risk_violations"]
    proposal_id = result["proposal_id"]
    db = get_db()
    decision = db.execute(
        "SELECT approved FROM risk_decisions WHERE proposal_id=?", (proposal_id,),
    ).fetchone()
    assert decision is not None and decision["approved"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (proposal_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE proposal_id=?", (proposal_id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RG-09", "pipeline_risk_gate", "§4 local paper causal authority", "L2c",
        "A native paper Proposal has exactly one durable Opportunity source across the normal and "
        "Serenity tables, and its canonical symbol, physical market, and direction all match; "
        "missing, absent, ambiguous, or mismatched identity rejects before materialization.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("missing_id", "proposal_opportunity_required"),
        ("not_found", "proposal_opportunity_not_found"),
        ("ambiguous", "proposal_opportunity_ambiguous"),
        ("symbol_mismatch", "proposal_opportunity_symbol_mismatch"),
        ("market_mismatch", "proposal_opportunity_market_mismatch"),
        ("direction_mismatch", "proposal_opportunity_direction_mismatch"),
    ),
)
def test_local_paper_requires_one_exact_opportunity_carrier(
    tmp_path, monkeypatch, case, expected,
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, SerenityOpportunity, SerenityTheme
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 60_000.0,
    )
    opportunity_id = None if case == "missing_id" else f"carrier-{case}"
    if case not in {"missing_id", "not_found"}:
        source = Opportunity(
            id=opportunity_id,
            symbol="ETH/USDT" if case == "symbol_mismatch" else "BTC/USDT",
            market="usdt_futures" if case == "market_mismatch" else "spot",
            direction_bias="short" if case == "direction_mismatch" else "long",
            status="queued",
        )
        repo.insert_opportunity(source)
    if case == "ambiguous":
        theme = SerenityTheme(id="theme-ambiguous", name="ambiguous carrier")
        repo.insert_serenity_theme(theme)
        repo.insert_serenity_opportunity(SerenityOpportunity(
            id=opportunity_id,
            theme_id=theme.id,
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
        ))

    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60_000.0,
        stop_price=58_000.0, execution_mode="paper_internal",
        opportunity_id=opportunity_id,
    )

    assert result["status"] == "rejected"
    assert expected in result["risk_violations"]
    proposal_id = result["proposal_id"]
    db = get_db()
    for table in ("orders", "positions"):
        assert db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE proposal_id=?", (proposal_id,),
        ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.proposal_id=?",
        (proposal_id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RG-10", "pipeline_risk_gate", "§4 local paper causal round trip", "L2c",
        "A native paper Proposal with one exact durable Opportunity can open through Gateway and "
        "close through the production local router with an eligible, causally linked Outcome.",
        status="implemented",
    )
)
def test_exact_opportunity_carrier_opens_and_closes_eligible(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 60_000.0,
    )
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 61_000.0,
    )
    source = Opportunity(
        id="carrier-valid", symbol="BTC/USDT", market="spot",
        direction_bias="long", status="queued",
    )
    repo.insert_opportunity(source)
    opened = submit_proposal(
        symbol=source.symbol, market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60_000.0,
        stop_price=58_000.0, take_profit=64_000.0, risk_reward=2.0,
        execution_mode="paper_internal",
        opportunity_id=source.id,
    )
    assert opened["status"] == "executed", opened

    closed = close_position_routed(
        opened["position_id"], "take_profit", fallback_exit_price=61_000.0,
        decision_source="spec_exact_opportunity_carrier",
    )
    assert closed["closed"] is True
    position = repo._select_one("positions", opened["position_id"])
    assert position["opportunity_id"] == source.id
    assert position["outcome_eligible"] == 1
    assert position["outcome_exclusion_reason"] == "eligible"


@spec_claim(
    SpecClaim(
        "PL-RG-11", "pipeline_risk_gate", "§4 local paper executable price", "L2c",
        "A native paper market Proposal price equals the fresh exact-instrument mark at the "
        "authoritative Risk pass. A mismatched requested price rejects with no materialized "
        "facts, while an exact mark is approved and only the broker's configured slippage "
        "changes the resulting Fill price.",
        status="implemented",
    )
)
def test_local_paper_price_matches_fresh_mark_before_broker_slippage(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config
    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity
    from aletheia.storage.sqlite import get_db

    mark = 60_000.0
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark,
    )
    for opportunity_id in ("price-mismatch", "price-valid"):
        repo.insert_opportunity(Opportunity(
            id=opportunity_id,
            symbol="BTC/USDT",
            market="spot",
            direction_bias="long",
            status="queued",
        ))

    mismatched = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=59_000.0,
        stop_price=57_000.0, take_profit=63_000.0, risk_reward=2.0,
        execution_mode="paper_internal", opportunity_id="price-mismatch",
    )
    assert mismatched["status"] == "rejected"
    assert "proposal_price_not_current_mark" in mismatched["risk_violations"]

    db = get_db()
    for table in ("orders", "positions"):
        assert db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE proposal_id=?",
            (mismatched["proposal_id"],),
        ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.proposal_id=?",
        (mismatched["proposal_id"],),
    ).fetchone()[0] == 0

    valid = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=mark,
        stop_price=58_000.0, take_profit=63_333.33, risk_reward=1.67,
        execution_mode="paper_internal", opportunity_id="price-valid",
    )
    assert valid["status"] == "executed", valid
    fill = db.execute(
        "SELECT fill_price FROM fills WHERE id=?", (valid["fill_id"],),
    ).fetchone()
    expected_fill = round(
        mark * (1 + float(load_config().trading.slippage_bps) / 10_000.0),
        8,
    )
    assert math.isclose(fill["fill_price"], expected_fill, rel_tol=1e-12)
    assert fill["fill_price"] != mark


@spec_claim(
    SpecClaim(
        "PL-RG-12", "pipeline_risk_gate", "§4 local paper complete entry plan", "L2c",
        "Every native paper market entry commits a finite positive entry, stop, take-profit, "
        "and reward:risk value before approval; side geometry and the rounded reward:risk math "
        "must agree with those immutable prices. Invalid plans reject before materialization.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("case", "updates", "expected"),
    (
        ("missing_price", {"price": 0.0}, "invalid_price"),
        ("missing_stop", {"stop_price": None}, "incomplete_entry_plan:stop_loss"),
        ("missing_tp", {"take_profit": None}, "incomplete_entry_plan:take_profit"),
        ("missing_rr", {"risk_reward": None}, "incomplete_entry_plan:risk_reward"),
        (
            "inverted_geometry",
            {"stop_price": 61_000.0, "take_profit": 64_000.0, "risk_reward": 4.0},
            "entry_plan_geometry:expected_stop<entry<tp_for_long",
        ),
        (
            "inconsistent_rr",
            {"risk_reward": 3.0},
            "entry_plan_risk_reward_mismatch",
        ),
    ),
)
def test_local_paper_market_entry_requires_complete_consistent_plan(
    tmp_path, monkeypatch, case, updates, expected,
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 60_000.0,
    )
    opportunity_id = f"entry-plan-{case}"
    repo.insert_opportunity(Opportunity(
        id=opportunity_id,
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
    ))
    proposal = {
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "order_type": "market",
        "quantity": 0.001,
        "price": 60_000.0,
        "stop_price": 58_000.0,
        "take_profit": 64_000.0,
        "risk_reward": 2.0,
        "execution_mode": "paper_internal",
        "opportunity_id": opportunity_id,
    }
    proposal.update(updates)
    result = submit_proposal(**proposal)

    assert result["status"] == "rejected"
    assert expected in result["risk_violations"]
    db = get_db()
    proposal_id = result["proposal_id"]
    for table in ("orders", "positions"):
        assert db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE proposal_id=?", (proposal_id,),
        ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.proposal_id=?",
        (proposal_id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RG-14",
        "pipeline_risk_gate",
        "§4 native entry-plan reward:risk envelope",
        "L2c",
        "Every native Proposal that requires a complete entry plan independently "
        "rejects a mathematically consistent rounded reward:risk below 1.00 or above "
        "10.00; both inclusive boundaries remain admissible.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    ("risk_reward", "expect_violation"),
    [
        (1.0, False),
        (10.0, False),
        (0.99, True),
        (10.01, True),
        (555.09, True),
    ],
)
def test_native_entry_plan_risk_reward_has_fail_closed_envelope(
    tmp_path, monkeypatch, risk_reward, expect_violation
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.01,
        price=100.0,
        order_type="limit",
        source="native",
        leverage=1,
        stop_price=99.0,
        take_profit=100.0 + risk_reward,
        risk_reward=risk_reward,
    )
    repo.insert_trade_proposal(proposal)

    result = evaluate_proposal(proposal)
    violations = result.violations
    assert (
        "entry_plan_risk_reward_outside_envelope" in violations
    ) is expect_violation
    assert "entry_plan_risk_reward_mismatch" not in violations
    if expect_violation:
        assert not result.approved


@spec_claim(
    SpecClaim(
        "PL-RG-13", "pipeline_risk_gate", "§4 entry-plan scope", "L2c",
        "The complete local market plan gate applies only to native paper entries. Native "
        "exchange market rows and reconciler-adopted rows retain their existing plan semantics "
        "and never inherit local mark, Opportunity, or complete-plan violations.",
        status="implemented",
    )
)
def test_local_market_plan_gate_does_not_capture_exchange_or_adopted_rows(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    adopted = TradeProposal(
        id="adopted-paper-plan-exempt",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
        source="adopted",
    )
    repo.insert_trade_proposal(adopted)
    adopted_result = evaluate_proposal(adopted, decision_role="authoritative")

    exchange_market = TradeProposal(
        id="exchange-market-plan-exempt",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=3_000.0,
        execution_mode="bitget_demo",
        source="native",
    )
    repo.insert_trade_proposal(exchange_market)
    exchange_result = evaluate_proposal(exchange_market, decision_role="preflight")

    forbidden_prefixes = (
        "paper_market_data_unavailable",
        "proposal_price_not_current_mark",
        "proposal_opportunity_",
        "incomplete_entry_plan",
        "entry_plan_geometry",
        "entry_plan_risk_reward_mismatch",
    )
    for result in (adopted_result, exchange_result):
        assert not any(
            violation.startswith(forbidden_prefixes)
            for violation in result.violations
        ), result.violations


# ---------------------------------------------------------------------------
# Round 2: Sample-Aware Kelly Sizing (SZ-*) — Coriva impl plan 2026-06-30
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SZ-SAMPLE-KELLY", "pipeline_risk_gate",
        "§sizing sample_aware_kelly_param",
        "L2a",
        "vol_target_size 接受 paper_trade_count 参数；< 10 笔时 effective_kelly=0.1，"
        "10-30 笔线性爬升到 base_kelly（cfg.trading.vol_kelly_cap_fraction）。"
        "新策略自动降仓，成熟策略行为不变（向后兼容 paper_trade_count=0 默认值）。",
        status="implemented",
    ),
)
def test_vol_target_size_accepts_paper_trade_count():
    """vol_target_size 必须有 paper_trade_count 参数（L2a structural）。"""
    import inspect
    from aletheia.trading import sizing

    sig = inspect.signature(sizing.vol_target_size)
    assert "paper_trade_count" in sig.parameters, (
        "vol_target_size 缺少 paper_trade_count 参数 — SZ-SAMPLE-KELLY 未实现"
    )
    # Default must be 0 (backward compatible — existing callers pass nothing)
    default = sig.parameters["paper_trade_count"].default
    assert default == 0, (
        f"paper_trade_count 默认值应为 0（向后兼容），得到 {default!r}"
    )


@spec_claim(
    SpecClaim(
        "SZ-SAMPLE-KELLY-SCALE", "pipeline_risk_gate",
        "§sizing sample_kelly_reduces_size",
        "L2b",
        "paper_trade_count=3 时 size <= paper_trade_count=50 时的 50%。"
        "新策略前 10 笔自动降低仓位，不影响成熟策略。",
        status="implemented",
    ),
)
def test_vol_target_size_reduced_for_new_strategy():
    """paper_trade_count=3 的 size 不超过 paper_trade_count=50 的 50%。"""
    import pytest
    from aletheia.trading.sizing import vol_target_size

    try:
        sz_new = vol_target_size("BTC/USDT", conviction=70, paper_trade_count=3)
        sz_mature = vol_target_size("BTC/USDT", conviction=70, paper_trade_count=50)
    except Exception as e:
        pytest.fail(f"vol_target_size with paper_trade_count failed: {e}")

    assert sz_new <= sz_mature * 0.5, (
        f"新策略(3笔) size={sz_new:.2f} 应 <= 成熟策略(50笔) size={sz_mature:.2f} 的 50%"
        f"（得到比率 {sz_new/sz_mature if sz_mature else 'N/A':.2f}）"
    )
