"""Phase 2 — doctor content-quality probes.

Each probe is exercised against a REAL temp SQLite DB (no mocks of the data layer,
per the project's no-mock-call-verify rule): we insert the anomalous shape and the
clean shape and assert the probe's severity verdict. A probe that always returned
"ok" (or always "warn") would fail these.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from aletheia.diagnostics import doctor as dx


@pytest.fixture
def db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    yield conn


def _ago(days=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()


def _ahead(days=0, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def _ins(conn, table, **cols):
    keys = ", ".join(cols)
    qs = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({qs})", tuple(cols.values()))
    conn.commit()


def _verdict(probe):
    r = dx.Report(generated_at="t")
    probe(r)
    assert r.checks, f"{probe.__name__} added no check"
    return r.checks[0]


# --- content.strategy_health ------------------------------------------------

def test_strategy_health_empty_is_info(db):
    assert _verdict(dx._probe_content_strategy_health).severity == "info"


def test_strategy_health_reports_legacy_count_as_info(db):
    # Rules engine deleted (Position C 2026-07-02): strategy_records is a frozen
    # legacy table. Any records → info with the count, never zombie WARN / ok.
    _ins(db, "strategy_records", id="s1", name="old", state="paper_running",
         created_at=_ago(days=10), paper_trading_paused=0)
    v = _verdict(dx._probe_content_strategy_health)
    assert v.severity == "info" and "legacy" in v.detail.lower()


# --- content.memo_quality ----------------------------------------------------

def test_memo_quality_absent_is_info(db):
    assert _verdict(dx._probe_content_memo_quality).severity == "info"


def test_memo_quality_expired_warns(db):
    import json
    _ins(db, "knowledge_items", id="m1", item_type="trading_memo", title="memo",
         tags=json.dumps({"valid_until": _ago(days=1)}),
         content=json.dumps({"regime_playbook": [], "valid_until": _ago(days=1)}),
         created_at=_ago(days=1))
    v = _verdict(dx._probe_content_memo_quality)
    assert v.severity == "warn" and "expired" in v.detail.lower()


def test_memo_quality_valid_is_ok(db):
    import json
    _ins(db, "knowledge_items", id="m2", item_type="trading_memo", title="memo",
         tags=json.dumps({"valid_until": _ahead(days=1)}),
         content=json.dumps({"regime_playbook": [{"r": 1}], "valid_until": _ahead(days=1)}),
         created_at=_ago(hours=1))
    assert _verdict(dx._probe_content_memo_quality).severity == "ok"


# --- content.lesson_chain ----------------------------------------------------

def _ins_lesson(conn, lesson_id, status, applied=None, created_at=None):
    _ins(conn, "ps_lessons", lesson_id=lesson_id, source_type="trade", source_id="x",
         category="risk", severity="info", summary="s", evidence_json="{}",
         status=status, created_at=created_at or _ago(hours=1),
         applied_policy_version=applied)


def test_lesson_chain_empty_is_info(db):
    assert _verdict(dx._probe_content_lesson_chain).severity == "info"


def test_lesson_chain_recent_proposed_is_info(db):
    # lesson_reviewer machinery is now wired; recently-proposed lessons are honestly
    # awaiting the daily evidence-gated review (cron 03:00 CST), NOT a dead chain.
    # Reporting warn here would false-alarm forever the day after every new lesson.
    for i in range(3):
        _ins_lesson(db, f"l{i}", "proposed")
    v = _verdict(dx._probe_content_lesson_chain)
    assert v.severity == "info" and "awaiting" in v.detail.lower()


def test_lesson_chain_stale_backlog_warns(db):
    # proposed lessons older than the 48h review window that never drained → the
    # wired reviewer is failing (cron dead / LLM unavailable). The probe must still
    # warn here: this is the genuine dead-chain failure, just detected by staleness.
    for i in range(3):
        _ins_lesson(db, f"l{i}", "proposed", created_at=_ago(hours=72))
    v = _verdict(dx._probe_content_lesson_chain)
    assert v.severity == "warn" and "draining" in v.detail.lower()


def test_lesson_chain_with_applied_is_ok(db):
    _ins_lesson(db, "l_ok", "accepted", applied="v1")
    assert _verdict(dx._probe_content_lesson_chain).severity == "ok"


# --- content.attribution -----------------------------------------------------

def _ins_sp(conn, i, opp, when=None):
    """Insert a signal_pending decision (the ranker output the probe measures)."""
    _ins(conn, "pi_decisions", id=f"d{i}", created_at=when or _ago(hours=1),
         symbol="BTC/USDT", direction="long", action="signal_pending",
         opportunity_id=opp)


def test_attribution_empty_is_info(db):
    assert _verdict(dx._probe_content_attribution).severity == "info"


def test_attribution_too_few_is_info(db):
    for i in range(10):  # < 20 → not enough to judge
        _ins_sp(db, i, None)
    assert _verdict(dx._probe_content_attribution).severity == "info"


def test_attribution_broken_links_warn(db):
    for i in range(30):  # all NULL opportunity_id → 100% > 10%
        _ins_sp(db, i, None)
    v = _verdict(dx._probe_content_attribution)
    assert v.severity == "warn" and "attribution" in v.detail.lower()


def test_attribution_recent_clean_is_ok(db):
    for i in range(30):
        _ins_sp(db, i, f"opp{i}")
    assert _verdict(dx._probe_content_attribution).severity == "ok"


def test_attribution_under_threshold_is_ok(db):
    for i in range(30):
        _ins_sp(db, i, None if i < 2 else f"opp{i}")  # 2/30 ≈ 7% < 10%
    assert _verdict(dx._probe_content_attribution).severity == "ok"


# --- content.shadow_trades ---------------------------------------------------

def test_shadow_trades_empty_is_info(db):
    assert _verdict(dx._probe_content_shadow_trades).severity == "info"


def test_shadow_trades_stale_warns(db):
    _ins(db, "shadow_trades", id="sh1", opportunity_id="o1", symbol="BTC/USDT",
         direction="long", created_at=_ago(days=2), resolved_at=None)
    assert _verdict(dx._probe_content_shadow_trades).severity == "warn"


def test_shadow_trades_resolved_is_ok(db):
    _ins(db, "shadow_trades", id="sh2", opportunity_id="o2", symbol="BTC/USDT",
         direction="long", created_at=_ago(days=2), resolved_at=_ago(days=1))
    assert _verdict(dx._probe_content_shadow_trades).severity == "ok"


# --- content.signal_settlement -----------------------------------------------

def _ins_outcome(conn, oid, status, due_at):
    _ins(conn, "universe_forward_outcomes", id=oid, member_id="m", snapshot_id="s",
         symbol="BTC/USDT", horizon="4h", entry_price_usd=100.0, entry_ts=_ago(days=3),
         due_at=due_at, status=status)


def test_signal_settlement_empty_is_info(db):
    assert _verdict(dx._probe_content_signal_settlement).severity == "info"


def test_signal_settlement_overdue_pending_warns(db):
    _ins_outcome(db, "o1", "pending", _ago(hours=1))  # due in the past, still pending
    assert _verdict(dx._probe_content_signal_settlement).severity == "warn"


def test_signal_settlement_current_is_ok(db):
    _ins_outcome(db, "o2", "settled", _ago(days=1))
    _ins_outcome(db, "o3", "pending", _ahead(days=1))  # not yet due
    assert _verdict(dx._probe_content_signal_settlement).severity == "ok"


# --- content.proposal_bias ---------------------------------------------------

def _ins_prop(conn, i, direction):
    _ins(conn, "trade_proposals", id=f"p{i}", symbol="BTC/USDT", market_type="perp",
         direction=direction, created_at=_ago(hours=1))


def test_proposal_bias_too_few_is_info(db):
    for i in range(3):
        _ins_prop(db, i, "short")
    assert _verdict(dx._probe_content_proposal_bias).severity == "info"


def test_proposal_bias_one_sided_warns_when_unmitigated(db, monkeypatch):
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision._proposal_direction_overheated",
        lambda direction, conviction: None,
    )
    for i in range(22):
        _ins_prop(db, i, "short")  # 100% short, >= 20 samples
    v = _verdict(dx._probe_content_proposal_bias)
    assert v.severity == "warn" and "bias" in v.detail.lower()


def test_proposal_bias_one_sided_info_when_direction_brake_active(db):
    for i in range(22):
        _ins_prop(db, i, "short")  # 100% short, >= 20 samples
    v = _verdict(dx._probe_content_proposal_bias)
    assert v.severity == "info"
    assert "brake active" in v.detail


def test_proposal_bias_mixed_is_ok(db):
    for i in range(11):
        _ins_prop(db, i, "short")
    for i in range(11, 22):
        _ins_prop(db, i, "long")  # 50/50, >= 20 samples
    assert _verdict(dx._probe_content_proposal_bias).severity == "ok"


# --- registration: all 7 content probes run inside build_report -------------

def test_all_content_probes_registered(db):
    names = {c.name for c in dx.build_report().checks}
    for expected in (
        "content.strategy_health", "content.memo_quality", "content.lesson_chain",
        "content.attribution", "content.shadow_trades", "content.signal_settlement",
        "content.proposal_bias",
    ):
        assert expected in names, f"content probe not registered: {expected}"
