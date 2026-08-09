"""Tests for health monitoring + honest alerting (Component 5).

Health is computed from REAL backend data. Alerts fire only on transition into an
unhealthy state (de-duped), and never fabricate a positive "healthy" push.
"""

import asyncio
import sqlite3

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
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: True)
    # Patch new tri-state helpers to healthy defaults so tests focused on
    # unrelated signals (LLM, code drift, …) get a clean baseline.
    monkeypatch.setattr(health, "_mo_ran_in_current_session", lambda: True)
    monkeypatch.setattr(
        health,
        "_observer_latest_session_run_info",
        lambda: {"status": "completed", "latest_opp_age_min": 5.0},
    )
    monkeypatch.setattr(health, "_market_data_stale_minutes", lambda cfg: 5.0)
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
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    import aletheia.telegram.bot as bot_mod
    monkeypatch.setattr(bot_mod, "TelegramBot", FakeBot)

    from aletheia.agent_runtime import health
    # We are inside a running loop (async test).  _send_alert uses fire-and-forget
    # (ensure_future) to avoid blocking the loop, so we yield control to let the
    # scheduled coroutine run.
    health._send_alert("test issue")
    await asyncio.sleep(0)

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
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)  # reset leaked global

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
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)  # reset leaked global
    sent = []
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    monkeypatch.setattr(health, "compute_health",
                        lambda: {"healthy": False, "issues": ["x"]})
    health.run_health_check()
    health.run_health_check()  # still unhealthy -> no second alert
    assert len(sent) == 1


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
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=100.0)
    snap.snapshot_at = snapshot_at
    insert_market_snapshot(snap)


def _isoformat_minutes_ago(minutes):
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()


def test_fresh_market_data_not_stale(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    _add_snapshot(_isoformat_minutes_ago(1))
    h = health.compute_health()
    assert not any("market_data_stale" in i for i in h["issues"])


def test_stale_market_data_flagged(tmp_path, monkeypatch):
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
    _add_snapshot(_isoformat_minutes_ago(120))  # 2h old, far past 30m default
    h = health.compute_health()
    assert h["healthy"] is False
    assert any("market_data_stale" in i for i in h["issues"])


def test_stale_market_data_not_flagged_when_paused(tmp_path, monkeypatch):
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.runtime.paused = True; save_config(cfg)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
    _add_snapshot(_isoformat_minutes_ago(120))
    h = health.compute_health()
    # Paused means data is expected to stop updating — must not false-alarm.
    assert not any("market_data_stale" in i for i in h["issues"])


def test_no_snapshots_is_not_stale(tmp_path, monkeypatch):
    import time
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
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

    monkeypatch.setattr(helpers, "get_provider", lambda name=None: _Boom())
    content, err = await helpers.llm_complete(system="s", user="u")
    assert content is None and "llm_unavailable" in err
    assert health.get_llm_consecutive_failures() == 1

    class _Ok:
        async def complete(self, req):
            class R: content = "hi"  # noqa: E701
            return R()

    monkeypatch.setattr(helpers, "get_provider", lambda name=None: _Ok())
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
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)
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
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)
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
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: False)
    h = health.compute_health()
    assert h["status"] == "degraded"
    assert any("exchange_unreachable" in i for i in h["issues"])


def test_market_data_never_collected_is_initializing_within_warmup(tmp_path, monkeypatch):
    """Scheduler active + no snapshot + within warmup window → initializing, not degraded."""
    import time
    real_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
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
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_fn)
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


def test_observer_no_run_is_initializing(tmp_path, monkeypatch):
    """Before MO runs in the current session, health must be initializing, not healthy."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    # Override: MO has not run yet.
    monkeypatch.setattr(health, "_mo_ran_in_current_session", lambda: False)
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert any("observer_initializing" in i for i in h["issues"])


def test_observer_valid_zero_opportunities_is_healthy(tmp_path, monkeypatch):
    """After MO completes with zero eligible opportunities, health must remain healthy."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_mo_ran_in_current_session", lambda: True)
    # Latest run completed, no opportunities produced — a legitimate zero result.
    monkeypatch.setattr(
        health,
        "_observer_latest_session_run_info",
        lambda: {"status": "completed", "latest_opp_age_min": None},
    )
    h = health.compute_health()
    assert h["status"] == "healthy"
    assert h["healthy"] is True
    assert not any("observer" in i for i in h["issues"])


def test_observer_run_failed_is_degraded(tmp_path, monkeypatch):
    """A failed MO run in the current session must surface as degraded."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_mo_ran_in_current_session", lambda: True)
    monkeypatch.setattr(
        health,
        "_observer_latest_session_run_info",
        lambda: {"status": "failed", "latest_opp_age_min": None},
    )
    h = health.compute_health()
    assert h["status"] == "degraded"
    assert any("observer_run_failed" in i for i in h["issues"])


def test_fresh_boot_before_probes_is_initializing(tmp_path, monkeypatch):
    """Simulation of a completely fresh boot: no probe, no snapshot, no MO run.

    Health must be 'initializing', never 'healthy', before any evidence exists.
    """
    import time
    real_market_fn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    # Restore real staleness fn to simulate fresh boot with no snapshot.
    monkeypatch.setattr(health, "_market_data_stale_minutes", real_market_fn)
    monkeypatch.setattr(health, "_process_start_time", time.monotonic())  # just started
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: None)  # no probe yet
    monkeypatch.setattr(health, "_mo_ran_in_current_session", lambda: False)  # no MO run yet
    h = health.compute_health()
    assert h["status"] == "initializing"
    assert h["healthy"] is False
    # The payload must NOT appear as degraded (no hard failures yet).
    assert not any(
        i in h["issues"] for i in ("exchange_unreachable", "market_data_stale", "observer_run_failed")
    )
