"""Tests for health monitoring + honest alerting (Component 5).

Health is computed from REAL backend data. Alerts fire only on transition into an
unhealthy state (de-duped), and never fabricate a positive "healthy" push.
"""

import sqlite3

from tests.position_fixture import force_update_position


def test_candidate_progress_observe_only_and_missing_profiles_are_not_stalls(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from tests.test_entry_planner import _typed_candidate
    setup_env(tmp_path, monkeypatch)
    start = datetime(2026, 9, 4, tzinfo=timezone.utc)
    for mechanism, family, capability in (("positioning_forced_flow", "derivatives", "observe_only"),
                                          ("information_catalyst", "catalyst", "unavailable")):
        candidate = _typed_candidate(mechanism=mechanism, family=family, capability=capability)
        repo.ensure_candidate_planning_generation(candidate.id, now=start)
        progress = repo.get_candidate_path_progress(now=start+timedelta(seconds=481))
        assert progress["ready_planning_count"] == 0
        assert "candidate_planning_stalled" not in progress["issues"]
    candidate = _typed_candidate(mechanism="continuation", family="momentum")
    repo.ensure_candidate_planning_generation(candidate.id, now=start)
    monkeypatch.setattr("aletheia.trading.entry_profiles.compatible_entry_profiles", lambda mechanism: ())
    assert repo.get_candidate_path_progress(now=start+timedelta(seconds=481))["ready_planning_count"] == 0


def test_candidate_progress_does_not_hold_writer_permit(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from aletheia.storage.sqlite import write_lock_held_by_current_thread
    from tests.test_entry_planner import _typed_candidate

    setup_env(tmp_path, monkeypatch)
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    candidate = _typed_candidate(mechanism="continuation", family="momentum")
    repo.ensure_candidate_planning_generation(candidate.id, now=now)
    writer_states = []

    def profiles(_mechanism):
        writer_states.append(write_lock_held_by_current_thread())
        return ("resting_maker",)

    monkeypatch.setattr("aletheia.trading.entry_profiles.compatible_entry_profiles", profiles)
    repo.get_candidate_path_progress(now=now)

    assert writer_states and not any(writer_states)


def test_candidate_progress_exact_epoch_and_entry_outcome_debt(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from aletheia.config.loader import load_config
    from aletheia.storage.models import (UniverseScannerRun, UniverseTwinShadowBatch,
        UniverseTwinShadowEvidence, FilterCandidateOutcome, EntryPlanRecord, Order, Fill)
    from tests.test_entry_lifecycle_cutover import _typed_candidate
    setup_env(tmp_path, monkeypatch)
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    cfg = load_config()
    cfg.universe = type(cfg.universe).model_validate({**cfg.universe.model_dump(),
        "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": "epoch-1"})
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: cfg)
    for index, epoch in enumerate(("old-epoch", "epoch-1")):
        run_id = f"health-shadow-{index}"
        repo.insert_universe_scanner_run(UniverseScannerRun(id=run_id, status="completed",
            started_at=(now-timedelta(minutes=2-index)).isoformat(), completed_at=now.isoformat(),
            metrics=json.dumps({"candidate_path": {"shadow_capture_status": "pending"}})))
        batch = UniverseTwinShadowBatch(scanner_run_id=run_id, policy_epoch_id=epoch,
            record_version="candidate-selection-utility-record/v3", authority_behavior_version="fixture",
            shadow_behavior_version="fixture", allocation_replay_contract="fixture", feature_source_fingerprint="f",
            metric_version="fixture", allocator_random_state_json="null", policy_json="{}",
            allocation_context_json="{}", candidate_count=1, batch_hash=f"hash-{index}", captured_at=now.isoformat())
        repo._insert("universe_twin_shadow_batches", batch.model_dump())
        evidence = UniverseTwinShadowEvidence(scanner_run_id=run_id, candidate_key=f"candidate-{index}",
            candidate_index=0, symbol="BTC/USDT", market_type="perp", authority_total_score=0,
            compact_output_json=json.dumps({"outcome_candidate_keys": {"long": f"selection-{index}"}}),
            input_json="{}", record_hash=f"record-{index}")
        repo._insert("universe_twin_shadow_records", evidence.model_dump())
    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=now)
    plan = EntryPlanRecord(id="health-plan", planning_generation_id=generation["id"], candidate_id=candidate.id,
        planning_key=generation["planning_key"], venue="bitget", symbol="BTC/USDT", market_type="perp",
        direction="long", execution_mode="RESTING_MAKER", plan_json="{}", plan_hash="a"*64,
        order_id="health-order", status="filled")
    repo._insert("entry_plans", plan.model_dump())
    repo.insert_order(Order(id="health-order", entry_plan_id=plan.id, symbol="BTC/USDT", market_type="perp",
        direction="long", order_type="limit", quantity=1, execution_mode="bitget_demo", status="filled",
        source="adopted"))  # Historical read fixture, not evidence of native execution authority.
    repo.insert_fill(Fill(id="health-fill", order_id="health-order", fill_price=100, fill_quantity=1))
    for key in ("selection-0", "selection-1", "entry-fill:unrelated"):
        repo.insert_filter_candidate_outcomes_batch([FilterCandidateOutcome(
            outcome_candidate_key=key, venue="bitget", symbol="BTC/USDT", market_type="perp", direction="long",
            horizon="4h", entry_price_usd=100, entry_ts=(now-timedelta(hours=5)).isoformat(),
            due_at=(now-timedelta(seconds=901)).isoformat(), producer_runtime_session_id="previous-process")])
    repo.execute_write("UPDATE filter_candidate_outcomes SET due_at=?,producer_runtime_session_id=? "
        "WHERE outcome_candidate_key='entry-fill:health-fill'",
        ((now-timedelta(seconds=901)).isoformat(), "previous-process"))
    progress = repo.get_candidate_path_progress(now=now)
    repo.execute_write("UPDATE universe_twin_shadow_records SET compact_output_json='not json' "
        "WHERE scanner_run_id='health-shadow-0'")
    assert repo.get_candidate_path_progress(now=now)["due_outcome_count"] == 2
    assert progress["due_outcome_count"] == 2
    assert "candidate_outcome_backlog" in progress["issues"]
    assert progress["shadow_capture_status"] == "captured"
    stored = repo.get_db().execute("SELECT metrics FROM universe_scanner_runs WHERE id='health-shadow-1'").fetchone()[0]
    assert json.loads(stored)["candidate_path"]["shadow_capture_status"] == "pending"
    repo.execute_write("UPDATE filter_candidate_outcomes SET due_at=? WHERE outcome_candidate_key IN (?,?)",
        ((now+timedelta(seconds=1)).isoformat(), "selection-1", "entry-fill:health-fill"))
    assert repo.get_candidate_path_progress(now=now)["due_outcome_count"] == 0
    repo.execute_write("UPDATE entry_plans SET order_id='missing-order' WHERE id='health-plan'")
    assert repo.get_candidate_path_progress(now=now)["availability"]["reason"] == "entry_outcome_identity_unavailable"


def test_candidate_progress_planning_uses_original_clocks_and_exact_identity(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from tests.test_entry_lifecycle_cutover import _typed_candidate
    setup_env(tmp_path, monkeypatch)
    start = datetime(2026, 9, 4, tzinfo=timezone.utc)
    candidate = _typed_candidate()
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id, now=start)
    now = start + timedelta(seconds=481)
    progress = repo.get_candidate_path_progress(now=now)
    assert progress["ready_planning_count"] == 1
    assert progress["oldest_ready_at"] == start.isoformat()
    assert "candidate_planning_stalled" in progress["issues"]
    claimed = repo.claim_next_candidate_planning("health-owner", now=now, lease_seconds=10)
    assert claimed["id"] == generation["id"]
    assert repo.get_candidate_path_progress(now=now)["ready_planning_count"] == 0
    expired = repo.get_candidate_path_progress(now=now+timedelta(seconds=191))
    assert expired["expired_claim_count"] == 1
    assert "candidate_planning_stalled" in expired["issues"]
    repo.execute_write("UPDATE candidate_planning_generations SET status='unavailable',retry_not_before=?,updated_at=? WHERE id=?",
                       ((now+timedelta(seconds=10)).isoformat(), now.isoformat(), generation["id"]))
    assert repo.get_candidate_path_progress(now=now)["ready_planning_count"] == 0
    repo.execute_write("UPDATE candidate_planning_generations SET retry_not_before=?,updated_at=? WHERE id=?",
                       (start.isoformat(), now.isoformat(), generation["id"]))
    assert "candidate_planning_stalled" in repo.get_candidate_path_progress(now=now)["issues"]
    repo.execute_write("UPDATE universe_members SET venue='wrong' WHERE id=?", (candidate.id,))
    invalid = repo.get_candidate_path_progress(now=now)
    assert invalid["ready_planning_count"] == 0
    assert invalid["availability"]["reason"] == "planning_identity_unavailable"


def test_candidate_progress_health_cadence_and_disabled(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from aletheia.agent_runtime import health, scheduler
    from aletheia.config.loader import load_config
    from aletheia.storage.models import UniverseScannerRun
    setup_env(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    repo.insert_universe_scanner_run(UniverseScannerRun(
        started_at=now.isoformat(), completed_at=now.isoformat(), status="completed",
        metrics='{"candidate_path":{"selected_count":0}}'))
    cfg = load_config()
    monkeypatch.setattr(health, "load_config", lambda: cfg)
    monkeypatch.setattr(scheduler, "scheduled_job_status", lambda job: {"registered": True, "paused": False})
    monkeypatch.setattr(scheduler, "_current_scheduler", lambda: SimpleNamespace(
        get_job=lambda name: SimpleNamespace(trigger=SimpleNamespace(interval=timedelta(seconds=45)))))
    health._refresh_candidate_path_progress()
    assert health.get_candidate_path_progress_snapshot()["ttl_seconds"] == 90
    assert health.compute_health()["healthy"] is True
    repo.execute_write("UPDATE universe_scanner_runs SET completed_at=?",
        ((now-timedelta(seconds=2*cfg.universe.refresh_interval_min*60+601)).isoformat(),))
    health._refresh_candidate_path_progress()
    assert "candidate_refresh_stalled" in health.compute_health()["issues"]
    monkeypatch.setattr(scheduler, "_current_scheduler", lambda: None)
    health._refresh_candidate_path_progress()
    assert health.get_candidate_path_progress_snapshot()["availability"]["reason"] == "not_configured"
    assert health.compute_health()["healthy"] is False
    cfg.universe.enabled = False
    health._refresh_candidate_path_progress()
    assert health.get_candidate_path_progress_snapshot()["availability"]["reason"] == "disabled"
    assert health.compute_health()["healthy"] is True


def test_candidate_progress_refresh_failure_and_valid_empty(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from aletheia.storage.models import UniverseScannerRun
    setup_env(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    repo.insert_universe_scanner_run(UniverseScannerRun(
        id="health-empty", started_at=(now-timedelta(minutes=1)).isoformat(),
        completed_at=now.isoformat(), status="completed", snapshot_id="empty-snapshot",
        metrics=json.dumps({"candidate_path": {"selected_count": 0, "planning_supported_count": 0,
            "observe_only_count": 0, "selection_reason_counts": {"hard_gate_failed": 3},
            "shadow_capture_status": "terminal"}}),
    ))
    progress = repo.get_candidate_path_progress(now=now)
    assert progress["availability"]["available"] is True
    assert progress["selected_count"] == 0
    assert progress["issues"] == []
    assert progress["last_snapshot_id"] == "empty-snapshot"
    repo.insert_universe_scanner_run(UniverseScannerRun(
        id="health-failed", started_at=(now+timedelta(seconds=1)).isoformat(), status="failed",
    ))
    failed = repo.get_candidate_path_progress(now=now+timedelta(seconds=2))
    assert failed["issues"] == ["candidate_refresh_failed"]
    assert failed["last_success_at"] == now.isoformat()


def test_candidate_progress_cache_health_and_alert_only(tmp_path, monkeypatch):
    from aletheia.agent_runtime import health
    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(health, "_candidate_path_progress", None)
    assert health.compute_health()["status"] == "initializing"
    monkeypatch.setattr(health, "_refresh_candidate_path_progress", lambda: None)
    monkeypatch.setattr(health, "_candidate_path_progress", {
        "generated_at": "2000-01-01T00:00:00+00:00", "ttl_seconds": 1200,
        "availability": {"available": True, "reason": None}, "issues": [],
    })
    assert health.get_candidate_path_progress_snapshot()["availability"]["reason"] == "cache_expired"
    assert health.compute_health()["healthy"] is False
    from datetime import datetime, timezone
    health._candidate_path_progress["generated_at"] = datetime.now(timezone.utc).isoformat()
    health._candidate_path_progress["issues"] = ["candidate_planning_stalled", "candidate_outcome_backlog"]
    monkeypatch.setattr(health, "_trigger_restart", lambda reason: (_ for _ in ()).throw(AssertionError(reason)))
    monkeypatch.setattr(health, "_send_alert", lambda message: None)
    result = health.run_health_check()
    assert result["status"] == "degraded"
    assert "candidate_outcome_backlog" in result["issues"]


def test_candidate_progress_probe_failure_replaces_cache(tmp_path, monkeypatch):
    from aletheia.agent_runtime import health
    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(repo, "get_candidate_path_progress", lambda: (_ for _ in ()).throw(RuntimeError("read failed")))
    health._refresh_candidate_path_progress()
    progress = health.get_candidate_path_progress_snapshot()
    assert progress["availability"] == {"available": False, "reason": "probe_failed"}
    assert progress["generated_at"] is not None
    assert health.compute_health()["healthy"] is False


async def _make_async_provider(provider):
    """Helper for tests: wrap sync mock provider in async."""
    return provider


from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AgentRun
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.agent_runtime import health
    # Capture the real function before patching so callers can restore it.
    real_market_stale = health._market_data_stale_minutes
    health.reset_llm_failures()
    from datetime import datetime, timezone
    monkeypatch.setattr(health, "_candidate_path_progress", {
        "generated_at": datetime.now(timezone.utc).isoformat(), "ttl_seconds": 1200,
        "availability": {"available": True, "reason": None}, "issues": [],
    })
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: True)
    # Patch new tri-state helpers to healthy defaults so tests focused on
    # unrelated signals (LLM, code drift, …) get a clean baseline.
    monkeypatch.setattr(health, "_market_data_stale_minutes", lambda cfg: 5.0)
    monkeypatch.setattr(health, "_market_data_stale_sources", lambda cfg: [])
    return real_market_stale


def _add_run(status):
    repo.insert_agent_run(AgentRun(agent_name="market_observer", permission_mode="Observe", status=status))


async def test_send_alert_delivers_from_running_loop(tmp_path, monkeypatch):
    """§1.3: _send_alert is called from sync run_health_check, which may itself run
    inside an event loop (FastAPI route). It must actually deliver — not fire-and-
    forget a task that gets GC'd — and must not raise."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.alert_via_telegram = True
    cfg.telegram.enabled = True
    save_config(cfg)

    sent: list = []

    class FakeBot:
        allowed_chat_ids = [111, 222]
        def configure(self):
            return True
        async def send_message(self, chat_id, text, *, parse_mode="HTML"):
            sent.append((chat_id, text))

    import extensions.telegram.bot as bot_mod
    monkeypatch.setattr(bot_mod, "TelegramBot", FakeBot)
    from aletheia import notifications
    from extensions.telegram.delivery import send_telegram_alert
    monkeypatch.setattr(notifications, "_telegram_sender", send_telegram_alert)

    from aletheia.agent_runtime import health
    # We are inside a running loop (async test). This previously scheduled an
    # un-awaited task and delivered nothing; now it must complete synchronously.
    health._send_alert("test issue")

    assert len(sent) == 2
    assert all("test issue" in text for _, text in sent)


def test_compute_health_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    for _ in range(5):
        _add_run("completed")
    h = health.compute_health()
    assert h["healthy"] is True
    assert h["status"] == "healthy"
    assert h["issues"] == []


def test_funding_audit_backlog_degrades_health_and_warns_doctor(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.diagnostics import doctor
    from aletheia.portfolio.funding_audit import insert_pending_funding_audit
    from aletheia.storage.models import AuditEvent

    with repo.transaction(immediate=True):
        insert_pending_funding_audit(AuditEvent(
            id="funding-audit-health",
            timestamp="2026-08-23T00:00:00+00:00",
            actor="system",
            source="paper_funding_settler",
            event_type="paper_position_funding_settled",
            entity_type="position",
            entity_id="position-health",
            before_state="{}",
            after_state="{}",
            message="funding audit pending",
        ))

    result = health.compute_health()
    assert result["status"] == "degraded"
    assert "funding_audit_mirror_backlog:1" in result["issues"]

    report = doctor.Report()
    doctor._probe_funding_audit_mirror(report)
    check = next(
        item for item in report.checks
        if item.name == "trust.audit_mirror_backlog"
    )
    assert check.severity == "warn"
    assert "1 funding audit mirror(s) pending" in check.detail
    assert "2026-08-23T00:00:00+00:00" in check.detail


def test_funding_recovery_states_degrade_health_and_doctor(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import Position

    for position_id, reason in (
        ("funding-terminal", "broker_funding_history_expired"),
        ("funding-retry", "incomplete_funding_economics"),
    ):
        repo.insert_position(Position(
            id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            current_price=110.0,
            execution_mode="bitget_demo",
        ))
        force_update_position(
            position_id,
            status="closed",
            realized_pnl=None,
            funding_source="unavailable",
            outcome_eligible=False,
            outcome_exclusion_reason=reason,
        )

    # An exclusion label alone is not evidence permitting cost recovery (§11.6).
    assert "broker_funding_recovery_pending:1" not in health.compute_health()["issues"]
    force_update_position(
        "funding-retry",
        exit_fill_verified=True,
        exit_price_source="broker_fill",
        entry_fee=0.1,
        exit_fee=0.1,
        funding_cost=None,
    )

    result = health.compute_health()
    assert result["status"] == "degraded"
    assert "broker_funding_history_expired:1" in result["issues"]
    assert "broker_funding_recovery_pending:1" in result["issues"]

    report = doctor.Report()
    doctor._probe_health(report)
    check = next(item for item in report.checks if item.name == "health.rollup")
    assert check.severity == "warn"
    assert "broker_funding_history_expired:1" in check.detail
    assert "broker_funding_recovery_pending:1" in check.detail


def test_funding_audit_probe_unavailable_is_not_reported_healthy(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.portfolio import funding_audit

    def unavailable():
        raise RuntimeError("audit schema unavailable")

    monkeypatch.setattr(funding_audit, "funding_audit_backlog", unavailable)
    result = health.compute_health()

    assert result["healthy"] is False
    assert result["status"] == "initializing"
    assert "funding_audit_mirror_unknown" in result["issues"]


def test_compute_health_flags_high_failure_ratio(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    for _ in range(4):
        _add_run("completed_with_errors")
    _add_run("completed")
    h = health.compute_health()
    assert h["healthy"] is False
    assert any("agent_failure_ratio" in i for i in h["issues"])


def _disable_self_heal(monkeypatch):
    """These tests predate self-heal and assert only the ALERTING contract. With
    self_heal_enabled defaulting True, run_health_check() would also run
    _heal_port_cleanup() against the REAL process table and emit extra alerts.
    Disable self-heal so the alert count reflects only the transition alert."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.health.self_heal_enabled = False; save_config(cfg)


def test_health_alert_fires_on_unhealthy_transition(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _disable_self_heal(monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_last_alerted_issue_keys", frozenset())

    sent = []
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    monkeypatch.setattr(health, "compute_health",
                        lambda: {"healthy": False, "issues": ["agent_failure_ratio:0.8>0.5"]})

    health.run_health_check()
    assert len(sent) == 1  # alerted on transition into unhealthy

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "health_alert" for e in read_jsonl("audit.jsonl"))


def test_health_alert_dedupes_consecutive(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _disable_self_heal(monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_last_alerted_issue_keys", frozenset())
    sent = []
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    monkeypatch.setattr(health, "compute_health",
                        lambda: {"healthy": False, "issues": ["x"]})
    health.run_health_check()
    health.run_health_check()  # still unhealthy -> no second alert
    assert len(sent) == 1


def test_durable_issue_does_not_suppress_new_incident(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _disable_self_heal(monkeypatch)
    from aletheia.agent_runtime import health

    monkeypatch.setattr(health, "_last_alerted_issue_keys", frozenset())
    sent = []
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    results = iter([
        {
            "healthy": False,
            "issues": ["broker_funding_history_expired:1"],
        },
        {
            "healthy": False,
            "issues": [
                "broker_funding_history_expired:1",
                "exchange_unreachable",
            ],
        },
    ])
    monkeypatch.setattr(health, "compute_health", lambda: next(results))

    health.run_health_check()
    health.run_health_check()

    assert len(sent) == 2
    assert "exchange_unreachable" in sent[1]


def test_health_no_alert_when_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    sent = []
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    monkeypatch.setattr(health, "compute_health", lambda: {"healthy": True, "issues": []})
    health.run_health_check()
    assert sent == []  # never fabricate a positive push


# --- Market-data staleness (zombie/silent-degradation detection) ---

def _add_snapshot(snapshot_at):
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot
    snap = MarketSnapshot(
        symbol="BTC/USDT",
        market_type="perp",
        exchange="binance",
        price=100.0,
    )
    snap.snapshot_at = snapshot_at
    insert_market_snapshot(snap)


def _restore_market_probe(monkeypatch, health, real_fn):
    from aletheia.trading import market_data

    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
    monkeypatch.setattr(
        market_data,
        "market_snapshot_target_groups",
        lambda _cfg: {("binance", "perp")},
    )


def _isoformat_minutes_ago(minutes):
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()


def test_fresh_market_data_not_stale(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _add_snapshot(_isoformat_minutes_ago(1))
    h = health.compute_health()
    assert not any("market_data_stale" in i for i in h["issues"])


def test_future_market_data_is_degraded(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    _add_snapshot((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

    h = health.compute_health()

    assert h["status"] == "degraded"
    assert "market_data_timestamp_future" in h["issues"]


def test_small_future_market_clock_skew_is_tolerated(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    _add_snapshot((datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())

    h = health.compute_health()

    assert h["status"] == "healthy"


def test_stale_market_data_flagged(tmp_path, monkeypatch):
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    _add_snapshot(_isoformat_minutes_ago(120))  # 2h old, far past 30m default
    h = health.compute_health()
    assert h["healthy"] is False
    assert any("market_data_stale" in i for i in h["issues"])


def test_market_data_staleness_names_lagging_source_group(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from aletheia.agent_runtime import health
    real_stale = health._market_data_stale_minutes
    real_sources = health._market_data_stale_sources
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import MarketSnapshot
    from aletheia.trading import market_data

    fresh = MarketSnapshot(
        symbol="RNVDA/USDT", market_type="spot", exchange="bitget", price=100.0
    )
    stale = MarketSnapshot(
        symbol="BTC/USDT", market_type="perp", exchange="binance", price=60_000.0
    )
    fresh.snapshot_at = datetime.now(timezone.utc).isoformat()
    stale.snapshot_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo.insert_market_snapshot_batch([fresh, stale])
    monkeypatch.setattr(
        market_data,
        "market_snapshot_target_groups",
        lambda _cfg: {("bitget", "spot"), ("binance", "perp")},
    )
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_stale)
    monkeypatch.setattr(health, "_market_data_stale_sources", real_sources)

    result = health.compute_health()

    assert result["status"] == "degraded"
    assert any(
        issue.endswith("source=binance/perp")
        for issue in result["issues"]
        if issue.startswith("market_data_stale:")
    )
    assert not any("source=bitget/spot" in issue for issue in result["issues"])


def test_empty_market_targets_ignore_fresh_and_stale_foreign_rows(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from aletheia.agent_runtime import health
    from aletheia.storage import repositories
    from aletheia.trading import market_data

    cfg = SimpleNamespace(
        runtime=SimpleNamespace(paused=False, scheduler_enabled=True),
        health=SimpleNamespace(market_data_max_staleness_min=30),
    )
    rows = [{
        "exchange": "foreign",
        "market_type": "spot",
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }]
    monkeypatch.setattr(repositories, "latest_market_snapshot_groups", lambda: rows)
    monkeypatch.setattr(market_data, "market_snapshot_target_groups", lambda _cfg: set())

    assert health._market_data_stale_minutes(cfg) is None
    assert health._market_data_stale_sources(cfg) == []

    rows[0]["snapshot_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert health._market_data_stale_minutes(cfg) is None
    assert health._market_data_stale_sources(cfg) == []


def test_empty_market_targets_do_not_hide_snapshot_query_error(tmp_path, monkeypatch):
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.storage import repositories
    from aletheia.trading import market_data

    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
    monkeypatch.setattr(market_data, "market_snapshot_target_groups", lambda _cfg: set())
    monkeypatch.setattr(
        repositories,
        "latest_market_snapshot_groups",
        lambda: (_ for _ in ()).throw(RuntimeError("db gone")),
    )

    result = health.compute_health()

    assert result["status"] == "initializing"
    assert "market_data_probe_error" in result["issues"]


def test_stale_market_data_not_flagged_when_paused(tmp_path, monkeypatch):
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.runtime.paused = True; save_config(cfg)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    _add_snapshot(_isoformat_minutes_ago(120))
    h = health.compute_health()
    # Paused means data is expected to stop updating — must not false-alarm.
    assert not any("market_data_stale" in i for i in h["issues"])


def test_no_snapshots_is_not_stale(tmp_path, monkeypatch):
    import time
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    # Pin _process_start_time to now so uptime is always well within the warmup
    # window regardless of how long the test process has been running.
    monkeypatch.setattr(health, "_process_start_time", time.monotonic())
    # Never collected any data yet — "not started", which is initializing, not stale.
    h = health.compute_health()
    assert not any("market_data_stale" in i for i in h["issues"])
    assert h["status"] == "initializing"
    assert any("market_data_initializing" in i for i in h["issues"])


# --- LLM consecutive-failure counter (fulfils llm_unavailable_alert_after) ---

def test_llm_failure_counter_flags_after_threshold(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    health.reset_llm_failures()
    # default llm_unavailable_alert_after = 3
    for _ in range(3):
        health.record_llm_failure()
    assert health.get_llm_consecutive_failures() == 3
    h = health.compute_health()
    assert h["healthy"] is False
    assert any("llm_unavailable" in i for i in h["issues"])


def test_llm_success_resets_counter(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    health.reset_llm_failures()
    for _ in range(3):
        health.record_llm_failure()
    health.record_llm_success()
    assert health.get_llm_consecutive_failures() == 0
    h = health.compute_health()
    assert not any("llm_unavailable" in i for i in h["issues"])


def test_llm_failures_below_threshold_not_flagged(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    health.reset_llm_failures()
    health.record_llm_failure()
    health.record_llm_failure()  # 2 < 3
    h = health.compute_health()
    assert not any("llm_unavailable" in i for i in h["issues"])


# --- version-drift guard (stale process running old code) ---

def test_code_drift_flagged_in_health(tmp_path, monkeypatch):
    """When build_info reports drift, compute_health surfaces it as unhealthy.

    compute_health imports code_drift_issue inside the function, so patch it on
    the build_info module (the import source), not on health.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, build_info
    monkeypatch.setattr(build_info, "code_drift_issue",
                        lambda: "code_updated_pending_restart:aaaaaaa..bbbbbbb")
    h = health.compute_health()
    assert h["healthy"] is False
    assert any(i.startswith("code_updated_pending_restart:") for i in h["issues"])


def test_no_code_drift_keeps_health_clean(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, build_info
    monkeypatch.setattr(build_info, "code_drift_issue", lambda: None)
    for _ in range(5):
        _add_run("completed")
    h = health.compute_health()
    assert h["healthy"] is True
    assert not any("code_updated_pending_restart" in i for i in h["issues"])


# --- llm_complete / llm_structured report success/failure into the counter ---

async def test_llm_complete_reports_failure_then_success(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.agents import helpers
    health.reset_llm_failures()

    class _Boom:
        async def complete(self, req):
            raise RuntimeError("provider down")

    monkeypatch.setattr(helpers, "get_provider_async", lambda name=None: _make_async_provider(_Boom()))
    content, err = await helpers.llm_complete(system="s", user="u")
    assert content is None and "llm_unavailable" in err
    assert health.get_llm_consecutive_failures() == 1

    class _Ok:
        async def complete(self, req):
            class R: content = "hi"  # noqa: E701
            return R()

    monkeypatch.setattr(helpers, "get_provider_async", lambda name=None: _make_async_provider(_Ok()))
    content, err = await helpers.llm_complete(system="s", user="u")
    assert content == "hi" and err is None
    assert health.get_llm_consecutive_failures() == 0


# --- code_drift one-shot: don't keep restarting if drift persists ---

def test_code_drift_does_not_restart_twice(tmp_path, monkeypatch):
    """After one code_drift restart in the last hour, if drift persists, fall
    through to market_data_stale instead of consuming another restart slot."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, build_info
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.health.self_heal_enabled = True
    cfg.health.self_heal_code_drift = True
    cfg.health.self_heal_market_data = True
    save_config(cfg)

    # Simulate a previous code_drift restart 20 minutes ago
    from aletheia.files.jsonl import write_audit
    from datetime import datetime, timedelta
    ts = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
    write_audit({
        "actor": "system", "source": "self_heal",
        "event_type": "self_heal_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "reason": "code_drift",
        "message": "Self-heal restart: code_drift",
        "timestamp": ts,
    })

    # code_drift still present + market_data is stale
    monkeypatch.setattr(build_info, "code_drift_issue",
                        lambda: "code_updated_pending_restart:aaa..bbb")
    monkeypatch.setattr(health, "_market_data_stale_minutes", lambda cfg: 60.0)
    monkeypatch.setattr(health, "_last_alerted_issue_keys", frozenset())
    monkeypatch.setattr(health, "_self_heal_suppressed_alerted", False)

    restarted_for = []
    monkeypatch.setattr(health, "_trigger_restart", lambda r: restarted_for.append(r))
    monkeypatch.setattr(health, "_can_self_restart", lambda: (True, ""))
    monkeypatch.setattr(health, "_send_alert", lambda msg: None)

    health.run_health_check()
    assert restarted_for == ["market_data_stale"]


def test_code_drift_first_time_still_restarts(tmp_path, monkeypatch):
    """First occurrence of code_drift (no prior restart in last hour) should
    still trigger a restart as before."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, build_info
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.health.self_heal_enabled = True
    cfg.health.self_heal_code_drift = True
    save_config(cfg)

    monkeypatch.setattr(build_info, "code_drift_issue",
                        lambda: "code_updated_pending_restart:aaa..bbb")
    monkeypatch.setattr(health, "_last_alerted_issue_keys", frozenset())
    monkeypatch.setattr(health, "_self_heal_suppressed_alerted", False)

    restarted_for = []
    monkeypatch.setattr(health, "_trigger_restart", lambda r: restarted_for.append(r))
    monkeypatch.setattr(health, "_can_self_restart", lambda: (True, ""))
    monkeypatch.setattr(health, "_send_alert", lambda msg: None)

    health.run_health_check()
    assert restarted_for == ["code_drift"]


# ---------------------------------------------------------------------------
# Tri-state health: initializing / unknown states (Issue #393)
# ---------------------------------------------------------------------------

def test_exchange_no_probe_is_unknown_not_healthy(tmp_path, monkeypatch):
    """No cached exchange probe must NOT be treated as reachable (issue #393)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.exchange.paper_execution_mode = "okx_demo"; save_config(cfg)
    # Override the default exchange patch to return None (unknown / no probe).
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: None)
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert h["healthy"] is False
    assert any("exchange_connectivity_unknown" in i for i in h["issues"])
    assert not any("exchange_unreachable" in i for i in h["issues"])


def test_exchange_confirmed_unreachable_is_degraded(tmp_path, monkeypatch):
    """A cached probe that reported failure must surface as degraded, not unknown."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.exchange.paper_execution_mode = "okx_demo"; save_config(cfg)
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: False)
    h = health.compute_health()
    assert h["status"] == "degraded"
    assert any("exchange_unreachable" in i for i in h["issues"])


def test_stale_exchange_success_becomes_unknown(tmp_path, monkeypatch):
    import time

    from aletheia.agent_runtime import connectivity, health
    from aletheia.config.loader import load_config, save_config

    real_exchange_reachable = health._exchange_reachable
    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.health.exchange_probe_interval_sec = 60
    save_config(cfg)
    monkeypatch.setattr(health, "_exchange_reachable", real_exchange_reachable)
    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": {"reachable": True, "ts": time.time() - 181}},
    )

    result = health.compute_health()

    assert result["status"] == "initializing"
    assert "exchange_connectivity_unknown" in result["issues"]


def test_startup_gate_probe_error_is_not_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, startup_behavior_gate

    monkeypatch.setattr(
        startup_behavior_gate,
        "read_gate_state",
        lambda: (_ for _ in ()).throw(RuntimeError("gate unavailable")),
    )

    result = health.compute_health()

    assert result["status"] == "initializing"
    assert "startup_gate_probe_error" in result["issues"]


def test_runtime_session_probe_error_is_not_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health, runtime_session

    monkeypatch.setattr(
        runtime_session,
        "current_session",
        lambda: (_ for _ in ()).throw(RuntimeError("session unavailable")),
    )

    result = health.compute_health()

    assert result["status"] == "initializing"
    assert "runtime_session_provenance_unknown" in result["issues"]


def test_market_data_never_collected_is_initializing_within_warmup(tmp_path, monkeypatch):
    """Scheduler active + no snapshot + within warmup window → initializing, not degraded."""
    import time
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _restore_market_probe(monkeypatch, health, real_fn)
    # Fresh process: uptime is well below the staleness threshold.
    monkeypatch.setattr(health, "_process_start_time", time.monotonic())
    # No snapshots in DB.
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert any("market_data_initializing" in i for i in h["issues"])
    assert not any("market_data_never_collected" in i for i in h["issues"])


def test_market_data_never_collected_becomes_degraded_after_deadline(tmp_path, monkeypatch):
    """Scheduler active + no snapshot + past warmup deadline → degraded."""
    import time
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    from aletheia.config.loader import load_config
    _restore_market_probe(monkeypatch, health, real_fn)
    # Pin process start time far in the past so deadline is exceeded.
    threshold_sec = load_config().health.market_data_max_staleness_min * 60
    monkeypatch.setattr(health, "_process_start_time", time.monotonic() - threshold_sec - 60)
    # No snapshots in DB.
    h = health.compute_health()
    assert h["status"] == "degraded"
    assert any("market_data_never_collected" in i for i in h["issues"])


def test_market_data_probe_error_surfaces_as_initializing(tmp_path, monkeypatch):
    """An exception in the staleness probe must not be silently dropped."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health

    def _boom(cfg):
        raise RuntimeError("db gone")

    monkeypatch.setattr(health, "_market_data_stale_minutes", _boom)
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert any("market_data_probe_error" in i for i in h["issues"])


def test_fresh_boot_before_probes_is_initializing(tmp_path, monkeypatch):
    """Simulation of a completely fresh boot: no probe, no snapshot.

    Health must be 'initializing', never 'healthy', before any evidence exists.
    """
    import time
    real_market_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    # Restore real staleness fn to simulate fresh boot with no snapshot.
    _restore_market_probe(monkeypatch, health, real_market_fn)
    monkeypatch.setattr(health, "_process_start_time", time.monotonic())  # just started
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: None)  # no probe yet
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert h["healthy"] is False
    # The payload must NOT appear as degraded (no hard failures yet).
    assert not any(
        i in h["issues"] for i in ("exchange_unreachable", "market_data_stale")
    )
