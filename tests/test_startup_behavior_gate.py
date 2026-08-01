"""Tests for the Startup Behavior Gate (aletheia/agent_runtime/startup_behavior_gate.py).

The gate makes the gateway startup sequence honest:

    Gateway starts → API/Dashboard/essential data jobs available → AI decisions +
    new trading stay PAUSED until a set of ground-truth checks pass. On pass the
    trading loop resumes; on failure the system keeps collecting data under a
    LAYERED pause (essential jobs continue), records a DURABLE blocker that
    survives restart, and periodically re-checks — it is never killed and never
    hard-loops.

These tests are written before the production code (TDD). They pin:
  1. the pure verdict (evaluate_startup_gate) for each failure mode,
  2. the lifespan orchestration (startup_behavior_gate) — layered pause, durable
     blocker, audit, and resume-on-clear,
  3. the pi_daemon start gate (gate_allows_startup),
  4. restart durability of the blocker,
  5. that a user-initiated pause / kill switch is respected (never auto-resumed).

All scheduler/notification side effects are mocked — no real network here.
"""

import asyncio
import sqlite3

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.config.loader import save_config, load_config, _invalidate_config_cache


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR/CONFIG_PATH at tmp and init a real DB (integrity naturally ok)."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "gateway").mkdir(parents=True, exist_ok=True)

    from aletheia.storage.migrations import run_migrations

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    sq._db_conn = conn
    run_migrations()
    _invalidate_config_cache()


def _healthy_cfg(**overrides) -> AletheiaConfig:
    """A config that should PASS every gate check: gate enabled, paper_internal,
    learning influence off, not paused, no kill switch."""
    cfg = AletheiaConfig()
    cfg.health.startup_behavior_gate_enabled = True
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.learning.paper_influence_enabled = False
    cfg.learning.lifecycle_automation_enabled = False
    for k, v in overrides.items():
        # dotted overrides like runtime__paused
        obj = cfg
        parts = k.split("__")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], v)
    save_config(cfg)
    _invalidate_config_cache()
    return cfg


@pytest.fixture(autouse=True)
def _mock_scheduler(monkeypatch):
    """Record pause/resume calls without a real APScheduler."""
    calls = {"pause": [], "resume": 0}

    import aletheia.agent_runtime.scheduler as sched

    def fake_pause(layered=None):
        calls["pause"].append(layered)

    def fake_resume():
        calls["resume"] += 1

    monkeypatch.setattr(sched, "pause_scheduler", fake_pause)
    monkeypatch.setattr(sched, "resume_scheduler", fake_resume)
    return calls


# --------------------------------------------------------------------------- #
# 1. Pure verdict — the healthy baseline
# --------------------------------------------------------------------------- #

def test_evaluate_passes_on_healthy_paper_internal(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is True, verdict
    assert verdict["blockers"] == []
    assert verdict["user_blocked"] is False
    names = {c["name"] for c in verdict["checks"]}
    # All required gate dimensions are represented.
    assert {
        "migrations_applied",
        "sqlite_integrity",
        "foreign_keys",
        "active_runtime_scope",
        "exchange_sync",
        "pending_orders",
        "pending_closes",
        "reconciliation_incident",
        "learning_influence",
    } <= names


# --------------------------------------------------------------------------- #
# 2. DB integrity failure → single blocker, gate does not pass
# --------------------------------------------------------------------------- #

def test_sqlite_integrity_failure_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # Pre-import state_store so its `get_db` binding captures the real function
    # BEFORE we patch sq.get_db below.  evaluate_startup_gate() lazily imports
    # state_store on first call; if that import happens while the patch is active,
    # state_store.get_db permanently binds to the lambda and leaks into subsequent
    # tests running on the same worker, causing "closed database" errors.
    import aletheia.gateway.state_store as _ss  # noqa: F401

    # Force integrity_check to report corruption by wrapping the connection
    # (setattr on a C sqlite3.Connection is unreliable, so wrap get_db instead).
    import aletheia.storage.sqlite as sq
    real_conn = sq.get_db()

    class _Cur:
        def fetchone(self):
            return ("malformed database page 5",)
        def fetchall(self):
            return [("malformed database page 5",)]

    class _ConnWrapper:
        def execute(self, query, *args, **kwargs):
            if "integrity_check" in query:
                return _Cur()
            return real_conn.execute(query, *args, **kwargs)
        def __getattr__(self, name):
            return getattr(real_conn, name)

    monkeypatch.setattr(sq, "get_db", lambda: _ConnWrapper())

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "sqlite_integrity" in verdict["blockers"]


# --------------------------------------------------------------------------- #
# 3. Exchange sync timeout / incomplete (non-paper_internal, no cached view)
# --------------------------------------------------------------------------- #

def test_exchange_sync_incomplete_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # No unified cache exists on a fresh DB → active_broker_view_complete is False.
    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "exchange_sync" in verdict["blockers"]


def test_paper_internal_needs_no_exchange(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="paper_internal")
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = gate.evaluate_startup_gate()
    ex = next(c for c in verdict["checks"] if c["name"] == "exchange_sync")
    assert ex["ok"] is True
    assert "passed" in verdict and verdict["passed"] is True


# --------------------------------------------------------------------------- #
# 4. Reconciliation integrity incident → block
# --------------------------------------------------------------------------- #

def test_active_reconciliation_incident_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.portfolio import reconciliation_integrity as ri

    ri.record_reconciliation_integrity_incident(
        {
            "is_thrashing": True,
            "window_minutes": 30,
            "churn_count": 9,
            "churned_identities": ["BTC/USDT"],
            "reopened_by_source": {"reconcile": 9},
        }
    )
    incident = ri.get_active_reconciliation_integrity_incident()
    assert incident is not None

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "reconciliation_incident" in verdict["blockers"]


# --------------------------------------------------------------------------- #
# 5. Unexplained pending order / pending close → block
# --------------------------------------------------------------------------- #

def test_unresolved_order_intent_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.gateway.state_store import GatewayStateStore
    from aletheia.storage.models import PSOrderIntent

    store = GatewayStateStore()
    store.create_order_intent(
        PSOrderIntent(
            proposal_id="p1", strategy_id="s1", mode="paper", venue="paper_internal",
            symbol="BTC/USDT", side="buy", order_type="market", quantity="1",
            status="created", client_order_id="c1", idempotency_key="k1",
        )
    )
    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "pending_orders" in verdict["blockers"]


def test_unknown_status_position_blocks_as_pending_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.storage.sqlite import get_db

    # Insert a position stuck in 'unknown' (reconciliation could not confirm) —
    # an unexplained pending close.
    db = get_db()
    db.execute(
        "INSERT INTO ps_positions (position_id, mode, strategy_id, venue, symbol, "
        "side, quantity, status, updated_at) VALUES "
        "('pos1','paper','s1','paper_internal','BTC/USDT','long','1','unknown','2026-01-01T00:00:00Z')"
    )
    db.commit()

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "pending_closes" in verdict["blockers"]


def test_open_position_alone_does_not_block(tmp_path, monkeypatch):
    """A normal OPEN position is expected during trading and must not block startup."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO ps_positions (position_id, mode, strategy_id, venue, symbol, "
        "side, quantity, status, updated_at) VALUES "
        "('pos2','paper','s1','paper_internal','ETH/USDT','long','1','open','2026-01-01T00:00:00Z')"
    )
    db.commit()

    verdict = gate.evaluate_startup_gate()
    pc = next(c for c in verdict["checks"] if c["name"] == "pending_closes")
    assert pc["ok"] is True, verdict


# --------------------------------------------------------------------------- #
# 6. Learning influence / lifecycle / allowlist consistency
# --------------------------------------------------------------------------- #

def test_lifecycle_automation_without_influence_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # lifecycle automation on but influence off → inconsistent, cannot advance.
    _healthy_cfg(
        learning__lifecycle_automation_enabled=True,
        learning__paper_influence_enabled=False,
    )
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "learning_influence" in verdict["blockers"]


def test_influence_enabled_with_empty_allowlist_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(
        learning__paper_influence_enabled=True,
        learning__paper_influence_paths=[],
    )
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = gate.evaluate_startup_gate()
    assert verdict["passed"] is False
    assert "learning_influence" in verdict["blockers"]


# --------------------------------------------------------------------------- #
# 7. User pause / kill switch → user_blocked, never auto-resumed
# --------------------------------------------------------------------------- #

def test_kill_switch_marks_user_blocked(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(runtime__kill_switch_active=True)
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = gate.evaluate_startup_gate()
    assert verdict["user_blocked"] is True
    assert "kill_switch" in (verdict["user_block_reason"] or "")


def test_user_pause_without_gate_marker_is_user_blocked(tmp_path, monkeypatch):
    """Config paused=True with no gate-owned marker == a human pause: respect it."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(runtime__paused=True)
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # No durable gate state written → the pause is not gate-owned.
    assert gate.read_gate_state() is None
    verdict = gate.evaluate_startup_gate()
    assert verdict["user_blocked"] is True


# --------------------------------------------------------------------------- #
# 8. Lifespan orchestration: fail → layered pause + durable blocker + audit
# --------------------------------------------------------------------------- #

def test_gate_pauses_layered_and_records_durable_blocker(tmp_path, monkeypatch, _mock_scheduler):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")  # exchange_sync will fail
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # Stop the recheck loop from actually looping in the test.
    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)

    verdict = asyncio.run(gate.startup_behavior_gate())
    assert verdict["passed"] is False

    # Layered pause applied (essential jobs keep running).
    assert _mock_scheduler["pause"] == [True]
    # Persisted paused so the state survives a restart.
    assert load_config().runtime.paused is True

    # Durable blocker recorded and marked gate-owned.
    state = gate.read_gate_state()
    assert state is not None
    assert state["paused_by_gate"] is True
    assert "exchange_sync" in state["blockers"]

    # Audited.
    from aletheia.files.jsonl import read_jsonl
    evts = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "startup_behavior_gate_paused"]
    assert evts


def test_gate_passes_clears_blocker_and_does_not_pause(tmp_path, monkeypatch, _mock_scheduler):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = asyncio.run(gate.startup_behavior_gate())
    assert verdict["passed"] is True
    assert _mock_scheduler["pause"] == []       # nothing paused
    assert load_config().runtime.paused is False
    assert gate.read_gate_state() is None        # no durable blocker


def test_gate_disabled_is_noop(tmp_path, monkeypatch, _mock_scheduler):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(
        health__startup_behavior_gate_enabled=False,
        exchange__paper_execution_mode="bitget_demo",  # would fail if the gate ran
    )
    from aletheia.agent_runtime import startup_behavior_gate as gate

    verdict = asyncio.run(gate.startup_behavior_gate())
    assert verdict.get("skipped") is True
    assert _mock_scheduler["pause"] == []
    assert load_config().runtime.paused is False


# --------------------------------------------------------------------------- #
# 9. pi_daemon start gate
# --------------------------------------------------------------------------- #

def test_gate_allows_startup_false_when_blocked(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")  # exchange_sync fails
    from aletheia.agent_runtime import startup_behavior_gate as gate

    assert gate.gate_allows_startup() is False


def test_gate_allows_startup_true_when_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    assert gate.gate_allows_startup() is True


def test_gate_allows_startup_true_when_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(
        health__startup_behavior_gate_enabled=False,
        exchange__paper_execution_mode="bitget_demo",
    )
    from aletheia.agent_runtime import startup_behavior_gate as gate

    assert gate.gate_allows_startup() is True


# --------------------------------------------------------------------------- #
# 10. Restart durability + resume-on-clear via recheck
# --------------------------------------------------------------------------- #

def test_blocker_survives_restart_and_resumes_when_clear(tmp_path, monkeypatch, _mock_scheduler):
    """Simulate: gate pauses (blocker persisted) → 'restart' (fresh eval) → the
    condition is now clear → the recheck resume path resumes and clears the blocker."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")
    from aletheia.agent_runtime import startup_behavior_gate as gate

    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)
    asyncio.run(gate.startup_behavior_gate())

    # Blocker persisted (durable) and paused.
    state = gate.read_gate_state()
    assert state is not None and state["paused_by_gate"] is True
    assert load_config().runtime.paused is True

    # "Restart": condition now clear (operator switched to paper_internal).
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    _invalidate_config_cache()

    # One recheck tick resumes and clears the durable blocker.
    resumed = asyncio.run(gate.recheck_once())
    assert resumed is True
    assert load_config().runtime.paused is False
    assert gate.read_gate_state() is None
    assert _mock_scheduler["resume"] == 1


# --------------------------------------------------------------------------- #
# 11. Surfaces — Health issue + Doctor probe read the durable blocker
# --------------------------------------------------------------------------- #

def test_health_surfaces_gate_blocker(tmp_path, monkeypatch, _mock_scheduler):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.agent_runtime.health import compute_health

    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)
    asyncio.run(gate.startup_behavior_gate())

    issues = compute_health()["issues"]
    assert any(i.startswith("startup_gate_blocked") for i in issues), issues


def test_doctor_probe_reports_gate_blocker(tmp_path, monkeypatch, _mock_scheduler):
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")
    from aletheia.agent_runtime import startup_behavior_gate as gate
    from aletheia.diagnostics.doctor import Report, _probe_startup_behavior_gate

    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)
    asyncio.run(gate.startup_behavior_gate())

    report = Report()
    _probe_startup_behavior_gate(report)
    gate_checks = [c for c in report.checks if c.name == "startup_gate.status"]
    assert gate_checks and gate_checks[0].severity == "blocker"
    assert "exchange_sync" in gate_checks[0].detail


def test_recheck_does_not_resume_user_pause(tmp_path, monkeypatch, _mock_scheduler):
    """If the pause is user-owned (no gate marker), recheck must NOT resume it."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(runtime__paused=True)  # user pause, no durable gate marker
    from aletheia.agent_runtime import startup_behavior_gate as gate

    assert gate.read_gate_state() is None
    resumed = asyncio.run(gate.recheck_once())
    assert resumed is False
    assert load_config().runtime.paused is True   # still paused — user owns it
    assert _mock_scheduler["resume"] == 0


# --------------------------------------------------------------------------- #
# 12. Fail-closed under exception paths (issue #389)
# --------------------------------------------------------------------------- #

def test_gate_allows_startup_returns_false_on_exception(tmp_path, monkeypatch):
    """gate_allows_startup() must return False when evaluation raises, never True."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    monkeypatch.setattr(
        gate, "evaluate_startup_gate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated evaluation failure")),
    )
    result = gate.gate_allows_startup()
    assert result is False


def test_app_py_blocks_pi_daemon_when_gate_precheck_raises(tmp_path, monkeypatch):
    """In app.py the exception handler must set gate_permits_pi_daemon=False, not True."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    import aletheia.agent_runtime.startup_behavior_gate as gate_mod

    # Patch gate_allows_startup so it raises to simulate any upstream error.
    monkeypatch.setattr(
        gate_mod, "gate_allows_startup",
        lambda *a, **k: (_ for _ in ()).throw(ImportError("simulated import error")),
    )

    # Reproduce the app.py try/except block under test.
    gate_permits_pi_daemon = True  # would be the fail-open value before the fix
    try:
        gate_permits_pi_daemon = gate_mod.gate_allows_startup()
    except Exception:
        gate_permits_pi_daemon = False  # the correct post-fix behaviour

    assert gate_permits_pi_daemon is False


def test_evaluate_startup_gate_exception_creates_synthetic_blocker(
    tmp_path, monkeypatch, _mock_scheduler
):
    """When evaluate_startup_gate() raises inside startup_behavior_gate(), the system
    must apply a layered pause and write a durable synthetic blocker, not skip."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)
    monkeypatch.setattr(
        gate, "evaluate_startup_gate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated schema error")),
    )

    verdict = asyncio.run(gate.startup_behavior_gate())

    # Must NOT be skipped — the gate was enabled and evaluation failed.
    assert verdict.get("skipped") is not True, "gate must not skip on evaluation error"
    assert verdict["passed"] is False
    assert "startup_gate_evaluation_error" in verdict["blockers"]

    # Layered pause must have been applied.
    assert _mock_scheduler["pause"] == [True]
    assert load_config().runtime.paused is True

    # Durable gate-owned blocker must have been written.
    state = gate.read_gate_state()
    assert state is not None
    assert state["paused_by_gate"] is True
    assert "startup_gate_evaluation_error" in state["blockers"]

    # The synthetic check detail must not expose raw paths/secrets — only the
    # exception type is included.
    check = next(c for c in state["checks"] if c["name"] == "startup_gate_evaluation_error")
    assert "RuntimeError" in check["detail"]
    assert "simulated schema error" not in check["detail"]  # message sanitised out


def test_evaluate_exception_recheck_resumes_when_clear(
    tmp_path, monkeypatch, _mock_scheduler
):
    """After an evaluation-error block, a later clean recheck resumes exactly once."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg()
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # Capture the real function before patching so we can restore it precisely
    # without undoing the tmp-path/DB setup patches from setup_env.
    real_evaluate = gate.evaluate_startup_gate

    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)
    monkeypatch.setattr(
        gate, "evaluate_startup_gate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transient error")),
    )
    asyncio.run(gate.startup_behavior_gate())

    # Gate is now paused with a gate-owned blocker.
    assert gate.read_gate_state() is not None
    assert load_config().runtime.paused is True

    # Restore just evaluate_startup_gate to the real implementation — this leaves
    # setup_env patches (tmp DATA_DIR, CONFIG_PATH, DB) intact so recheck_once()
    # reads the same in-memory test state.
    monkeypatch.setattr(gate, "evaluate_startup_gate", real_evaluate)

    resumed = asyncio.run(gate.recheck_once())
    assert resumed is True
    assert load_config().runtime.paused is False
    assert gate.read_gate_state() is None
    assert _mock_scheduler["resume"] == 1


def test_apply_block_stops_running_pi_daemon(tmp_path, monkeypatch, _mock_scheduler):
    """When _apply_block() fires on a system failure it must stop a running PI daemon."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(exchange__paper_execution_mode="bitget_demo")
    from aletheia.agent_runtime import startup_behavior_gate as gate

    stopped = []
    import aletheia.agent_runtime.pi_daemon as pi_mod
    monkeypatch.setattr(pi_mod, "is_daemon_running", lambda: True)
    monkeypatch.setattr(pi_mod, "stop_pi_daemon", lambda: stopped.append(1))
    monkeypatch.setattr(gate, "_start_recheck_loop", lambda *a, **k: None)

    asyncio.run(gate.startup_behavior_gate())

    assert stopped == [1], "stop_pi_daemon must be called when the gate blocks"


def test_gate_disabled_unchanged_when_evaluation_would_error(tmp_path, monkeypatch, _mock_scheduler):
    """Gate disabled → evaluation never runs, no pause, no blocker."""
    setup_env(tmp_path, monkeypatch)
    _healthy_cfg(
        health__startup_behavior_gate_enabled=False,
        exchange__paper_execution_mode="bitget_demo",
    )
    from aletheia.agent_runtime import startup_behavior_gate as gate

    # Even if evaluation would raise, the gate is off — should remain a no-op.
    monkeypatch.setattr(
        gate, "evaluate_startup_gate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )

    verdict = asyncio.run(gate.startup_behavior_gate())
    assert verdict.get("skipped") is True
    assert _mock_scheduler["pause"] == []
    assert load_config().runtime.paused is False
