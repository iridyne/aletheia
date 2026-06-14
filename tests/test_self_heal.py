"""Tests for the deterministic self-heal layer in run_health_check().

Self-heal ACTS on the issue strings compute_health() produces — no LLM. It must:
  - restart (exit 42) on code-drift / stale-feed, but ONLY when a supervisor or
    autostart will respawn us, and only within rate limits;
  - clean orphan/port-holder PIDs without killing self or the supervisor;
  - never restart on LLM-down / kill-switch / bad-strategy (alert-only);
  - never silently do nothing — a suppressed restart still alerts + audits.

Every test patches health._os_exit so nothing actually exits the test process.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


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
    # Reset module-level dedupe globals so suite order can't leak. Rate limiting
    # is NOT a global anymore — it reads from the audit log, which is fresh per
    # tmp_path, so nothing to reset there.
    from aletheia.agent_runtime import health
    health.reset_llm_failures()
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)
    monkeypatch.setattr(health, "_self_heal_suppressed_alerted", False)


def _seed_restart_audit(seconds_ago):
    """Write a self_heal_restart audit event dated `seconds_ago` in the past, so
    _can_self_restart() (which reads the persisted log) sees prior restart history
    exactly as it would across a real os._exit restart."""
    from datetime import datetime, timedelta
    from aletheia.files.jsonl import write_audit
    ts = (datetime.utcnow() - timedelta(seconds=seconds_ago)).isoformat()
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "timestamp": ts, "message": "Self-heal restart: code_drift",
    })


def _patch_health(monkeypatch, issues):
    """Make compute_health() return a fixed unhealthy verdict and capture alerts.

    Returns (exits, sent): exits is the list of codes passed to the patched
    _os_exit (which here returns instead of terminating); sent is alert messages.
    """
    from aletheia.agent_runtime import health
    exits: list[int] = []
    sent: list[str] = []
    monkeypatch.setattr(health, "compute_health",
                        lambda: {"healthy": issues == [], "issues": issues})
    monkeypatch.setattr(health, "_send_alert", lambda msg: sent.append(msg))
    monkeypatch.setattr(health, "_os_exit", lambda code: exits.append(code))
    return exits, sent


def _audit_event_types(types_wanted=None):
    from aletheia.files.jsonl import read_jsonl
    return [e.get("event_type") for e in read_jsonl("audit.jsonl")]


# --- Scenario 1: code drift -> restart (the original zombie root cause) ---

def test_code_drift_triggers_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "supervisor_owns_pid", lambda: True, raising=False)
    # _can_self_restart imports supervisor_owns_pid from pidfile at call time.
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    exits, sent = _patch_health(monkeypatch, ["code_updated_pending_restart:aaaaaaa..bbbbbbb"])
    health.run_health_check()

    assert exits == [42]  # restart was triggered
    events = _audit_event_types()
    assert "self_heal_restart" in events
    # the restart-reason audit message records the scenario
    from aletheia.files.jsonl import read_jsonl
    restart_rows = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "self_heal_restart"]
    assert restart_rows and "code_drift" in restart_rows[-1]["message"]


def test_code_drift_no_supervisor_suppresses_and_alerts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    import aletheia.gateway.autostart as autostart
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: False)
    monkeypatch.setattr(autostart, "status", lambda: {"enabled": False})

    exits, sent = _patch_health(monkeypatch, ["code_updated_pending_restart:aaaaaaa..bbbbbbb"])
    health.run_health_check()

    assert exits == []  # MUST NOT exit — restart would be fatal with no respawner
    events = _audit_event_types()
    assert "self_heal_suppressed" in events
    assert "self_heal_restart" not in events
    assert any("suppressed" in m for m in sent)


# --- Scenario 2: stale feed -> restart ---

def test_stale_feed_triggers_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    exits, sent = _patch_health(monkeypatch, ["market_data_stale:120min"])
    health.run_health_check()

    assert exits == [42]
    from aletheia.files.jsonl import read_jsonl
    restart_rows = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "self_heal_restart"]
    assert restart_rows and "market_data_stale" in restart_rows[-1]["message"]


# --- Rate limiting (reads PERSISTED audit log — survives restart) ---

def test_cooldown_blocks_second_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)
    # A restart happened 60s ago — within the 1800s default cooldown.
    _seed_restart_audit(seconds_ago=60)

    # Use market_data_stale (not code_drift) so _code_drift_already_tried()
    # doesn't short-circuit before reaching the cooldown check.
    exits, sent = _patch_health(monkeypatch, ["market_data_stale:120min"])
    health.run_health_check()

    assert exits == []  # cooldown blocks it
    assert "self_heal_suppressed" in _audit_event_types()
    assert any("cooldown" in m for m in sent)


def test_hourly_ceiling_blocks_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)
    # Two restarts within the hour but BOTH older than the 1800s cooldown, so the
    # cooldown rule passes and only the per-hour ceiling (default 2) can block.
    _seed_restart_audit(seconds_ago=3000)
    _seed_restart_audit(seconds_ago=2500)

    # Use market_data_stale (not code_drift) so _code_drift_already_tried()
    # doesn't short-circuit before reaching the ceiling check.
    exits, sent = _patch_health(monkeypatch, ["market_data_stale:120min"])
    health.run_health_check()

    assert exits == []
    assert any("ceiling" in m for m in sent)


def test_rate_limit_survives_simulated_restart(tmp_path, monkeypatch):
    """The bug end-to-end testing caught: a memory-based limit is wiped by the
    os._exit restart, so it loops every cycle. This proves the limit is read from
    the persisted audit log — a restart event written by a PRIOR process (here,
    seeded directly) is honored by THIS process with no shared in-memory state."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)
    # Prior process restarted 30s ago and then exited; only the audit row remains.
    _seed_restart_audit(seconds_ago=30)

    # A persistent, restart-resistant issue (e.g. external feed down) recurs.
    exits, sent = _patch_health(monkeypatch, ["market_data_stale:200min"])
    health.run_health_check()

    assert exits == []  # MUST NOT restart again — cooldown read from disk
    assert any("cooldown" in m for m in sent)


# --- Master toggle off -> legacy alert-only ---

def test_self_heal_disabled_is_legacy_alert_only(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.health.self_heal_enabled = False; save_config(cfg)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    exits, sent = _patch_health(monkeypatch, ["code_updated_pending_restart:a..b"])
    health.run_health_check()

    assert exits == []  # no self-heal action
    events = _audit_event_types()
    assert "health_alert" in events  # legacy transition alert still fires
    assert "self_heal_restart" not in events
    assert "self_heal_suppressed" not in events


# --- Scenario 4: LLM failures are alert-only, never restart ---

def test_llm_unavailable_never_restarts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    exits, sent = _patch_health(monkeypatch, ["llm_unavailable:5"])
    health.run_health_check()

    assert exits == []  # restart can't fix an external LLM outage
    events = _audit_event_types()
    assert "health_alert" in events  # transition alert
    assert "self_heal_restart" not in events
    assert "self_heal_suppressed" not in events


def test_kill_switch_never_restarts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    exits, sent = _patch_health(monkeypatch, ["kill_switch_active"])
    health.run_health_check()

    assert exits == []  # human-set state — never auto-restart away
    assert "self_heal_restart" not in _audit_event_types()


# --- Scenario 3: port cleanup ---

def test_port_cleanup_excludes_self_and_supervisor(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    import os

    me = os.getpid()
    supervisor = me + 1
    stray = me + 2
    killed: list[int] = []
    monkeypatch.setattr(pidfile, "read_pid", lambda: supervisor)
    monkeypatch.setattr(pidfile, "find_gateway_orphans", lambda: [me, supervisor, stray])
    monkeypatch.setattr(pidfile, "find_port_holders", lambda port: [])
    monkeypatch.setattr(pidfile, "kill_pid_tree", lambda p: killed.append(p))

    # Drive an unhealthy state that is NOT drift/stale so cleanup branch is reached.
    exits, sent = _patch_health(monkeypatch, ["agent_failure_ratio:0.9>0.5"])
    health.run_health_check()

    assert killed == [stray]  # never kill self or supervisor
    assert "self_heal_port_cleanup" in _audit_event_types()


def test_port_cleanup_noop_when_no_orphans(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    import os

    killed: list[int] = []
    monkeypatch.setattr(pidfile, "read_pid", lambda: os.getpid() + 1)
    monkeypatch.setattr(pidfile, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(pidfile, "find_port_holders", lambda port: [])
    monkeypatch.setattr(pidfile, "kill_pid_tree", lambda p: killed.append(p))

    exits, sent = _patch_health(monkeypatch, ["agent_failure_ratio:0.9>0.5"])
    health.run_health_check()

    assert killed == []
    assert "self_heal_port_cleanup" not in _audit_event_types()


# --- Transition dedupe regression (existing behavior must survive) ---

def test_transition_alert_dedupes_with_self_heal_on(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)

    # Use an alert-only issue so neither call restarts; both should keep running.
    exits, sent = _patch_health(monkeypatch, ["agent_failure_ratio:0.9>0.5"])
    # port cleanup branch runs but finds nothing
    monkeypatch.setattr(pidfile, "read_pid", lambda: None)
    monkeypatch.setattr(pidfile, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(pidfile, "find_port_holders", lambda port: [])

    health.run_health_check()
    health.run_health_check()  # still unhealthy -> no second transition alert

    health_alerts = [m for m in sent if "agent_failure_ratio" in m]
    assert len(health_alerts) == 1


# --- supervisor_owns_pid unit ---

def test_supervisor_owns_pid_logic(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.gateway.pidfile as pidfile
    import os

    monkeypatch.setattr(pidfile, "read_pid", lambda: os.getpid())
    assert pidfile.supervisor_owns_pid() is False  # we own it -> no supervisor

    monkeypatch.setattr(pidfile, "read_pid", lambda: os.getpid() + 1)
    assert pidfile.supervisor_owns_pid() is True  # someone else owns it -> supervisor

    monkeypatch.setattr(pidfile, "read_pid", lambda: None)
    assert pidfile.supervisor_owns_pid() is False  # no pidfile -> bare/foreground
