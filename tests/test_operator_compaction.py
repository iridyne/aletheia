import sqlite3
from datetime import datetime, timezone

from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()


def _init_test_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_build_operator_daily_summary_writes_required_sections(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.compaction import build_operator_daily_summary
    from aletheia.agent_shell.operator_store import (
        append_message,
        create_event,
        create_task,
        record_heartbeat,
    )

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    target_date = datetime.now(timezone.utc).date().isoformat()

    append_message(
        session_id="operator-main",
        role="user",
        content={"text": "Do not touch live trading."},
        message_type="command",
        related_event_id=None,
    )
    create_event(
        event_type="repair_executed",
        source="operator_loop",
        related_entity_type="task",
        related_entity_id="task-1",
        payload={"summary": "Restarted the market scanner safely."},
    )
    create_event(
        event_type="proposal_rejected",
        source="operator_loop",
        related_entity_type="proposal",
        related_entity_id="proposal-1",
        payload={"summary": "ETH short proposal rejected by risk."},
    )
    create_event(
        event_type="strategy_paused",
        source="operator_loop",
        related_entity_type="strategy",
        related_entity_id="strategy-1",
        payload={"summary": "Momentum strategy paused after review."},
    )
    create_task(
        task_type="diagnose_system_health",
        status="failed",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:45m"},
        error="market_data_stale:45m",
    )
    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Degraded",
        permission_mode="ReadOnly",
        is_busy=False,
        current_task_id=None,
        current_intent="monitoring system state",
        queue_size=0,
        last_error="market_data_stale:45m",
        model_provider="pi-shell",
        session_id="operator-main",
    )

    result = build_operator_daily_summary(date_str=target_date)

    assert result["path"].exists()
    content = result["path"].read_text(encoding="utf-8")
    assert "## Major User Instructions" in content
    assert "Do not touch live trading." in content
    assert "## Important Repair Decisions" in content
    assert "Restarted the market scanner safely." in content
    assert "## Proposal Outcomes" in content
    assert "ETH short proposal rejected by risk." in content
    assert "## Strategy-State Changes" in content
    assert "Momentum strategy paused after review." in content
    assert "## Unresolved Issues" in content
    assert "market_data_stale:45m" in content
    assert "## Follow-Up Recommendations" in content
    assert "Gateway is paused." in content


def test_agent_compact_command_creates_daily_summary(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import append_message
    target_date = datetime.now(timezone.utc).date().isoformat()
    target_token = target_date.replace("-", "")

    append_message(
        session_id="operator-main",
        role="user",
        content={"text": "Summarize today."},
        message_type="command",
        related_event_id=None,
    )

    result = runner.invoke(app, ["agent", "compact", "--date", target_date])

    assert result.exit_code == 0
    assert f"daily_summary_{target_token}.md" in result.output
