"""Tests for health monitoring + honest alerting (Component 5).

Health is computed from REAL backend data. Alerts fire only on transition into an
unhealthy state (de-duped), and never fabricate a positive "healthy" push.
"""

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


def _add_run(status):
    repo.insert_agent_run(AgentRun(agent_name="market_observer", permission_mode="Research", status=status))


def test_compute_health_healthy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health
    for _ in range(5):
        _add_run("completed")
    h = health.compute_health()
    assert h["healthy"] is True
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


def test_health_alert_fires_on_unhealthy_transition(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
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
